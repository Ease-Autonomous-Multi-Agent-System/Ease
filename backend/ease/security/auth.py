"""Password hashing (bcrypt) and JWT access/refresh tokens with refresh rotation + revocation."""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass

import bcrypt
import jwt

from ease.config import get_settings

ALGORITHM = "HS256"
ISSUER = "ease"
# bcrypt only looks at the first 72 bytes; reject longer passwords rather than silently truncating.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LEN = 10
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt(rounds=12))


class AuthError(Exception):
    pass


def validate_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise AuthError("password is too long (max 72 bytes)")
    if password.isalpha() or password.isdigit():
        raise AuthError("password must mix letters with digits or symbols")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return False
    if password_hash is None:
        # Unknown email: still spend a bcrypt round so response time doesn't reveal which emails exist.
        bcrypt.checkpw(password.encode(), _DUMMY_HASH)
        return False
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def _secret() -> str:
    s = get_settings().jwt_secret.get_secret_value()
    if len(s) < 32:
        raise AuthError("JWT_SECRET is missing or too short (need >= 32 chars)")
    return s


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    refresh_jti: str
    expires_in: int


def _encode(sub: str, typ: str, ttl: int, jti: str | None = None) -> str:
    now = int(time.time())
    claims = {"sub": sub, "typ": typ, "iat": now, "nbf": now, "exp": now + ttl, "iss": ISSUER}
    if jti:
        claims["jti"] = jti
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def issue_tokens(user_id: uuid.UUID | str) -> TokenPair:
    s = get_settings()
    jti = secrets.token_urlsafe(16)
    return TokenPair(
        access_token=_encode(str(user_id), "access", s.jwt_access_ttl_s),
        refresh_token=_encode(str(user_id), "refresh", s.jwt_refresh_ttl_s, jti),
        refresh_jti=jti,
        expires_in=s.jwt_access_ttl_s,
    )


def decode_token(token: str, expected_type: str) -> dict:
    try:
        claims = jwt.decode(
            token,
            _secret(),
            algorithms=[ALGORITHM],  # pinned: never accept "none" or an attacker-chosen algorithm
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "typ", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError("invalid or expired token") from exc
    if claims.get("typ") != expected_type:
        raise AuthError("wrong token type")
    return claims
