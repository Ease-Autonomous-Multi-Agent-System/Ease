#!/bin/sh
# Ease single-container start: prepare the database once, then hand every process to supervisord.
set -eu

# Public website mode (default): visitors bring their own keys, so sign-up can be open.
# If the operator switches it off to let everyone use the server's keys, sign-up must need an invite code.
case "${USER_KEYS_ONLY:-true}" in
  true|1|yes) echo "Public mode: every user runs Ease on their own AI and service keys" ;;
  *) : "${REGISTRATION_INVITE_CODE:?USER_KEYS_ONLY is off, so visitors would spend the server's keys: set REGISTRATION_INVITE_CODE}"
     if [ -z "${GROQ_API_KEY:-}${GEMINI_API_KEY:-}" ]; then
       echo "WARNING: USER_KEYS_ONLY is off but neither GROQ_API_KEY nor GEMINI_API_KEY is set" >&2
     fi ;;
esac

DATA="${DATA_DIR:-/home/user/data}"
mkdir -p "$DATA/artifacts" "$DATA/uploads" "$DATA/models" /tmp/nginx
umask 077

# Secrets not provided as Space secrets are generated per start. That is safe here because the database is
# rebuilt on every start too, so nothing encrypted with an old key survives.
if [ -z "${JWT_SECRET:-}" ]; then
  JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
fi
if [ -z "${VAULT_MASTER_KEY:-}" ]; then
  VAULT_MASTER_KEY="$(python -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())')"
fi
export JWT_SECRET VAULT_MASTER_KEY

# PostgreSQL: local only (127.0.0.1 + a private socket dir), password auth with a random password.
PGBIN=/usr/lib/postgresql/16/bin
PGDATA="$DATA/pg"
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  python -c 'import secrets; print(secrets.token_urlsafe(24))' > "$DATA/pg.pw"
  "$PGBIN/initdb" -D "$PGDATA" -U ease --pwfile="$DATA/pg.pw" --auth=scram-sha-256 -E UTF8 >/dev/null
fi
PGPASS="$(cat "$DATA/pg.pw")"
export DATABASE_URL="postgresql+psycopg://ease:${PGPASS}@127.0.0.1:5432/ease"
export PGPASSWORD="$PGPASS"

"$PGBIN/pg_ctl" -D "$PGDATA" -o "-h 127.0.0.1 -k /tmp -p 5432" -w -l "$DATA/pg-setup.log" start >/dev/null
"$PGBIN/psql" -h 127.0.0.1 -U ease -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='ease'" | grep -q 1 \
  || "$PGBIN/createdb" -h 127.0.0.1 -U ease ease
(cd /app/backend && alembic upgrade head)
"$PGBIN/pg_ctl" -D "$PGDATA" -w stop >/dev/null
unset PGPASSWORD

echo "Ease is starting on port 7860"
exec supervisord -c /app/deploy/supervisord.conf
