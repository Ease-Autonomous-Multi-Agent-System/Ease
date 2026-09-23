"""Shared API dependencies: current user, rate limiting, signed artifact URLs."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ease.config import get_settings
from ease.db.models import User
from ease.db.session import get_db
from ease.security.auth import AuthError, decode_token
from ease.security.ratelimit import RateLimiter

bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str:
    # uvicorn --proxy-headers only trusts X-Forwarded-For from --forwarded-allow-ips, so this is not spoofable
    return request.client.host if request.client else "unknown"


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated", {"WWW-Authenticate": "Bearer"})
    try:
        claims = decode_token(creds.credentials, "access")
        user = db.get(User, uuid.UUID(claims["sub"]))
    except (AuthError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token",
                            {"WWW-Authenticate": "Bearer"}) from exc
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account not found or disabled")
    return user


def limit(key: str, max_hits: int, window_s: int) -> None:
    res = RateLimiter().hit(key, max_hits, window_s)
    if not res.allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many requests, slow down",
                            {"Retry-After": str(res.retry_after_s)})


# ---------- signed artifact URLs ----------
def _sign_key() -> bytes:
    return hashlib.sha256(b"artifact-urls|" + get_settings().jwt_secret.get_secret_value().encode()).digest()


def sign_artifact(uri: str | None, ttl_s: int = 900) -> str | None:
    if not uri:
        return None
    exp = int(time.time()) + ttl_s
    sig = hmac.new(_sign_key(), f"{uri}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"/files/{quote(uri)}?exp={exp}&sig={sig}"


def verify_artifact(uri: str, exp: int, sig: str) -> bool:
    if exp < time.time():
        return False
    good = hmac.new(_sign_key(), f"{uri}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(good, sig)
