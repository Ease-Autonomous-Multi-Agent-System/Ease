"""Admin commands for a self-hosted Ease server. Run them inside the API container:

    docker compose exec api python -m ease.admin list-users
    docker compose exec api python -m ease.admin reset-password you@example.com
    docker compose exec api python -m ease.admin clear-rate-limits

The new password is typed at a hidden prompt, so it never appears on screen, in shell history or in logs.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from ease.db.models import AuditLog, User
from ease.db.session import session_scope
from ease.security.auth import AuthError, hash_password, validate_password_strength


def list_users() -> int:
    with session_scope() as s:
        users = list(s.scalars(select(User).order_by(User.created_at)))
        for u in users:
            print(f"{u.email:40} {u.full_name or '':24} created {u.created_at:%Y-%m-%d}")
        print(f"{len(users)} account(s)")
    return 0


def reset_password(email: str) -> int:
    with session_scope() as s:
        user = s.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            print(f"No account with the email {email!r}. See: python -m ease.admin list-users", file=sys.stderr)
            return 1
        for _ in range(3):
            pw = getpass.getpass("New password (min 10 characters, letters and digits): ")
            if pw != getpass.getpass("Repeat it: "):
                print("The two passwords differ, try again.")
                continue
            try:
                validate_password_strength(pw)
            except AuthError as exc:
                print(f"{exc} - try again.")
                continue
            user.password_hash = hash_password(pw)
            s.add(AuditLog(user_id=user.id, action="auth.password_reset_by_admin"))
            print(f"Password changed for {user.email}. Sign in with it now.")
            return 0
    print("Password not changed.", file=sys.stderr)
    return 1


def clear_rate_limits() -> int:
    """Unblock sign-up / sign-in after "too many requests" (e.g. many test accounts from one machine)."""
    from ease.security.ratelimit import get_redis

    r = get_redis()
    keys = [k for pattern in ("rl:register:*", "rl:login:*", "rl:refresh:*") for k in r.scan_iter(pattern)]
    for k in keys:
        r.delete(k)
    print(f"Cleared {len(keys)} sign-up/sign-in rate-limit counter(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ease.admin", description="Ease admin commands")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-users", help="show every account's email")
    rp = sub.add_parser("reset-password", help="set a new password for an account (asks for it)")
    rp.add_argument("email")
    sub.add_parser("clear-rate-limits", help="unblock sign-up / sign-in after 'too many requests'")
    args = ap.parse_args(argv)
    if args.cmd == "list-users":
        return list_users()
    if args.cmd == "reset-password":
        return reset_password(args.email)
    return clear_rate_limits()


if __name__ == "__main__":
    sys.exit(main())
