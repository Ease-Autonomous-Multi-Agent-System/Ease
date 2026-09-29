"""Website logins the user hands to Ease when the browser agent hits a sign-in page.

Stored in the vault as credential "login:<host>" (AES-256-GCM, like every secret), as JSON {username, password}.
Rules, all enforced by code:
  * the AI never sees them - BrowserAgent._sign_in types them with Playwright, outside any prompt;
  * they are only typed on the exact host they were given for, and only over https (or a local test site);
  * without "remember", they are deleted when the run ends (finalize / cancel), with a 2-day safety expiry.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from ease.logs import log

_TEMP_KEY = "templogin:{task_id}"
_TEMP_TTL_S = 2 * 24 * 3600


def save_login(session, user_id: uuid.UUID, host: str, username: str, password: str, *, remember: bool,
               task_id: uuid.UUID | str) -> None:
    from ease.db.models import CredentialKind
    from ease.security.ratelimit import get_redis
    from ease.security.vault import Vault

    secret = json.dumps({"username": username, "password": password})
    Vault(session).put(user_id, "login", host, secret, kind=CredentialKind.PASSWORD, hint=_hint(username))
    if not remember:
        key = _TEMP_KEY.format(task_id=task_id)
        r = get_redis()
        r.sadd(key, f"{user_id}|{host}")
        r.expire(key, _TEMP_TTL_S)


def load_login(user_id: str | None, host: str | None) -> dict[str, str] | None:
    if not user_id or not host:
        return None
    from ease.db.session import session_scope
    from ease.security.vault import Vault

    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        return None
    try:
        with session_scope() as s:
            raw = Vault(s).get(uid, f"login:{host}")
    except Exception:
        log.warning("site_login.lookup_failed", host=host)
        return None
    try:
        data: Any = json.loads(raw) if raw else None
    except ValueError:
        return None
    if isinstance(data, dict) and data.get("username") is not None and data.get("password"):
        return {"username": str(data["username"]), "password": str(data["password"])}
    return None


def forget_temporary(task_id: str) -> int:
    """Delete the logins given for this run without "remember". Called when the run ends or is cancelled."""
    from ease.db.session import session_scope
    from ease.security.ratelimit import get_redis
    from ease.security.vault import Vault

    try:
        r = get_redis()
        key = _TEMP_KEY.format(task_id=task_id)
        members = r.smembers(key)
        with session_scope() as s:
            for m in members:
                user_id, host = (m.decode() if isinstance(m, bytes) else m).split("|", 1)
                Vault(s).delete(uuid.UUID(user_id), f"login:{host}")
        r.delete(key)
        return len(members)
    except Exception:
        log.warning("site_login.forget_failed", task_id=task_id)
        return 0


def _hint(username: str) -> str:
    """What the settings page shows for a saved login: the username, partly masked."""
    u = username.strip()
    return u if len(u) <= 3 else u[:3] + "…" + (u.split("@", 1)[1] if "@" in u else "")
