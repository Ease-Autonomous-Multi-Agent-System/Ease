"""Create .env from .env.example with fresh random secrets - the only setup step before `docker compose up`.

    python scripts/setup_env.py

Fills JWT_SECRET, VAULT_MASTER_KEY and the database password (in POSTGRES_PASSWORD and DATABASE_URL, which must
match). Never overwrites an existing .env: its VAULT_MASTER_KEY decrypts the API keys users already saved.
Standard library only, so it runs before anything is installed.
"""

from __future__ import annotations

import base64
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    example, target = ROOT / ".env.example", ROOT / ".env"
    if target.exists():
        print(".env already exists - left unchanged (delete it first to start over).")
        return 0
    db_password = secrets.token_urlsafe(18)
    values = {
        "JWT_SECRET": secrets.token_urlsafe(48),
        "VAULT_MASTER_KEY": base64.b64encode(os.urandom(32)).decode(),
        "POSTGRES_PASSWORD": db_password,
    }
    text = example.read_text(encoding="utf-8")
    for name, value in values.items():
        text = re.sub(rf"^{name}=.*$", f"{name}={value}", text, flags=re.MULTILINE)
    text = text.replace("postgresql+psycopg://ease:change-me@", f"postgresql+psycopg://ease:{db_password}@")
    if "change-me" in text:
        print("warning: a 'change-me' placeholder is still in .env - check it", file=sys.stderr)
    target.write_text(text, encoding="utf-8", newline="\n")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print("Created .env with fresh secrets.")
    print("Optional: paste GROQ_API_KEY / GEMINI_API_KEY into .env now, or add your keys later in the app")
    print("under Profile & apps -> AI model keys.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
