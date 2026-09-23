"""Registration, login, refresh-token rotation, logout, profile."""

from __future__ import annotations

import hmac
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ease.api.deps import client_ip, current_user, limit
from ease.config import get_settings
from ease.db.models import AuditLog, User
from ease.db.session import get_db
from ease.schemas.api import LoginIn, MeOut, ProfileIn, RefreshIn, RegisterIn, TokenOut
from ease.security.auth import (
    AuthError,
    decode_token,
    hash_password,
    issue_tokens,
    validate_password_strength,
    verify_password,
)
from ease.security.ratelimit import LlmBudget, get_redis

router = APIRouter(tags=["auth"])


def _issue(user: User) -> TokenOut:
    pair = issue_tokens(user.id)
    # Refresh tokens are single-use: the jti is allow-listed in Redis and removed when used (rotation).
    get_redis().set(f"refresh:{pair.refresh_jti}", str(user.id), ex=get_settings().jwt_refresh_ttl_s)
    return TokenOut(access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in)


@router.post("/auth/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    limit(f"register:{client_ip(request)}", 5, 3600)
    code = get_settings().registration_invite_code
    if code and code.get_secret_value():
        if not body.invite_code or not hmac.compare_digest(body.invite_code, code.get_secret_value()):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "a valid invite code is required to register")
    try:
        validate_password_strength(body.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if db.scalar(select(User.id).where(User.email == body.email.lower())):
        # Same status as success-path validation errors would leak less, but a clear message is more useful here;
        # the endpoint is rate limited, which bounds enumeration.
        raise HTTPException(status.HTTP_409_CONFLICT, "an account with this email already exists")
    user = User(email=body.email.lower(), password_hash=hash_password(body.password), full_name=body.full_name)
    db.add(user)
    db.flush()
    db.add(AuditLog(user_id=user.id, action="auth.register", meta_json={"ip": client_ip(request)}))
    return _issue(user)


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    ip = client_ip(request)
    s = get_settings()
    limit(f"login:ip:{ip}", s.login_attempts_per_minute * 4, 60)
    limit(f"login:email:{body.email.lower()}", s.login_attempts_per_minute, 60)  # slows password guessing
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not verify_password(body.password, user.password_hash if user else None) or not user.is_active:
        db.add(AuditLog(user_id=user.id if user else None, action="auth.login_failed", meta_json={"ip": ip}))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "incorrect email or password")
    db.add(AuditLog(user_id=user.id, action="auth.login", meta_json={"ip": ip}))
    return _issue(user)


@router.post("/auth/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    limit(f"refresh:{client_ip(request)}", 30, 60)
    try:
        claims = decode_token(body.refresh_token, "refresh")
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token") from exc
    owner = get_redis().getdel(f"refresh:{claims.get('jti')}")
    if owner != claims["sub"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh token already used or revoked")
    user = db.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account disabled")
    return _issue(user)


@router.post("/auth/logout", status_code=204)
def logout(body: RefreshIn) -> None:
    try:
        claims = decode_token(body.refresh_token, "refresh")
        get_redis().delete(f"refresh:{claims.get('jti')}")
    except AuthError:
        pass


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user)) -> MeOut:
    s = get_settings()
    return MeOut(
        id=user.id, email=user.email, full_name=user.full_name, profile=user.profile_json or {},
        usage=LlmBudget().usage(str(user.id)),
        limits={"llm_calls_per_day": s.user_llm_calls_per_day, "tasks_per_hour": s.user_tasks_per_hour,
                "concurrent_tasks": s.user_concurrent_tasks},
    )


@router.patch("/me", response_model=MeOut)
def update_me(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> MeOut:
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.profile is not None:
        user.profile_json = {k: v for k, v in body.profile.items() if isinstance(k, str) and len(k) <= 60}
    db.add(user)
    return me(user)
