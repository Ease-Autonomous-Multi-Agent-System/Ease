"""Registration, login, refresh-token rotation, logout, profile, account deletion."""

from __future__ import annotations

import hmac
import shutil
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ease.api.deps import client_ip, current_user, limit
from ease.config import get_settings
from ease.db.models import AuditLog, Task, TaskStatus, User
from ease.db.session import get_db
from ease.schemas.api import DeleteAccountIn, LoginIn, MeOut, ProfileIn, RefreshIn, RegisterIn, TokenOut
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

TURNSTILE_VERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def captcha_ok(token: str | None, ip: str | None) -> bool:
    """Cloudflare Turnstile check. Fails closed: if Cloudflare cannot be reached, nobody can sign up."""
    secret = get_settings().turnstile_secret_key
    if not token:
        return False
    try:
        r = httpx.post(TURNSTILE_VERIFY, timeout=10, data={
            "secret": secret.get_secret_value() if secret else "", "response": token, **({"remoteip": ip} if ip else {})})
        return bool(r.json().get("success"))
    except (httpx.HTTPError, ValueError):
        return False


@router.get("/auth/config")
def auth_config() -> dict:
    """What the sign-up page needs to show (public: no secrets)."""
    s = get_settings()
    invite = s.registration_invite_code
    return {"turnstile_site_key": s.turnstile_site_key if s.turnstile_secret_key else "",
            "invite_required": bool(invite and invite.get_secret_value())}


def _issue(user: User) -> TokenOut:
    pair = issue_tokens(user.id)
    # Refresh tokens are single-use: the jti is allow-listed in Redis and removed when used (rotation).
    get_redis().set(f"refresh:{pair.refresh_jti}", str(user.id), ex=get_settings().jwt_refresh_ttl_s)
    return TokenOut(access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in)


@router.post("/auth/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    limit(f"register:{client_ip(request)}", 5, 3600)
    secret = get_settings().turnstile_secret_key
    if secret and secret.get_secret_value() and not captcha_ok(body.captcha_token, client_ip(request)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the human check failed; reload the page and try again")
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
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> MeOut:
    from ease.llm.router import ai_key_status

    s = get_settings()
    return MeOut(
        ai=ai_key_status(db, user.id), remember_logins=s.remember_site_logins,
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
    return me(user, db)


# LangGraph's checkpoint tables (PostgresSaver); keyed by thread_id, so they are not covered by the foreign keys.
_CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


@router.post("/me/delete", status_code=204)
def delete_account(body: DeleteAccountIn, request: Request, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> None:
    """Permanently delete the account and everything stored for it (right to erasure, DPDP Act 2023): profile,
    saved keys and website logins, documents, tasks with their steps, screenshots and checkpoints. Audit entries
    are kept without the user id. The password is asked again so a stolen session alone cannot erase an account."""
    limit(f"delete:{user.id}", 5, 3600)
    if not verify_password(body.password, user.password_hash):
        # 403, not 401: the session is valid, and the web app treats 401 as "signed out"
        raise HTTPException(status.HTTP_403_FORBIDDEN, "incorrect password")
    tasks = list(db.scalars(select(Task).where(Task.user_id == user.id)))
    if any(t.status == TaskStatus.RUNNING for t in tasks):
        raise HTTPException(status.HTTP_409_CONFLICT, "a task is still running; cancel it, then delete the account")
    s = get_settings()
    threads = [t.thread_id for t in tasks]
    for table in _CHECKPOINT_TABLES:
        if threads and db.scalar(text("select to_regclass(:t)"), {"t": table}):
            db.execute(text(f"delete from {table} where thread_id = any(:ids)"), {"ids": threads})  # noqa: S608
    files = [s.uploads_dir / str(user.id)] + [s.artifacts_dir / str(t.id) for t in tasks]
    db.add(AuditLog(user_id=None, action="account.deleted", meta_json={"tasks": len(tasks)}))
    db.delete(user)  # credentials, documents, tasks, templates: ON DELETE CASCADE
    db.commit()
    for folder in files:  # after the commit: a failed delete never leaves an account without its files
        shutil.rmtree(folder, ignore_errors=True)
