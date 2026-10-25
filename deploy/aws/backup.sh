#!/bin/bash
# Daily backup (cron, 03:30): the database and uploaded resumes, kept for 7 days in /opt/ease-backups.
# The files hold everyone's encrypted keys, so the folder is root-only. The keys stay safe only while
# VAULT_MASTER_KEY (in deploy/aws/.env) is NOT stored next to the backups if you copy them off the server.
set -euo pipefail
cd /opt/ease/deploy/aws
OUT=/opt/ease-backups
mkdir -p "$OUT" && chmod 700 "$OUT"
STAMP=$(date +%Y%m%d-%H%M)
umask 077

docker compose exec -T ease sh -c \
  'PGPASSWORD="$(cat /home/user/data/pg.pw)" /usr/lib/postgresql/16/bin/pg_dump -h 127.0.0.1 -U ease -Fc ease' \
  > "$OUT/db-$STAMP.dump.tmp"
mv "$OUT/db-$STAMP.dump.tmp" "$OUT/db-$STAMP.dump"   # a failed dump never replaces a good one
docker compose exec -T ease tar -C /home/user/data -czf - uploads > "$OUT/uploads-$STAMP.tgz"

find "$OUT" -name 'db-*.dump' -mtime +7 -delete
find "$OUT" -name 'uploads-*.tgz' -mtime +7 -delete
find "$OUT" -name '*.tmp' -mtime +1 -delete
echo "backup ok: $OUT/db-$STAMP.dump"
