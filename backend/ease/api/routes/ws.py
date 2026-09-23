"""WebSocket hub (component C7) + signed artifact file serving.

Auth: the client first calls POST /ws-ticket (normal Bearer auth) and gets a single-use ticket valid for 60 s,
bound to (user, task). The socket URL carries only that ticket, never the JWT - URLs end up in proxy and
browser logs. The Origin header is checked against the CORS allowlist (blocks cross-site WebSocket hijacking).
"""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import select

from ease.api.deps import current_user, limit, verify_artifact
from ease.api.routes.tasks import _sign_fields, owned_task, submit_decision
from ease.config import get_settings
from ease.db.models import TaskEvent, User
from ease.db.session import session_scope
from ease.events.emitter import channel
from ease.logs import log
from ease.schemas.api import ApproveIn, WsTicketIn
from ease.security.ratelimit import get_redis

router = APIRouter(tags=["realtime"])
TICKET_TTL = 60
MAX_CLIENT_MSG = 16_000


@router.post("/ws-ticket")
def ws_ticket(body: WsTicketIn, user: User = Depends(current_user)) -> dict[str, Any]:
    limit(f"ws-ticket:{user.id}", 60, 60)
    with session_scope() as db:
        owned_task(db, user, body.task_id)
    ticket = secrets.token_urlsafe(24)
    get_redis().set(f"wsticket:{ticket}", f"{user.id}|{body.task_id}", ex=TICKET_TTL)
    return {"ticket": ticket, "expires_in": TICKET_TTL}


def _backlog(task_id: uuid.UUID, after: int) -> list[dict[str, Any]]:
    with session_scope() as db:
        rows = db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.seq > after)
                          .order_by(TaskEvent.seq).limit(2000))
        return [{"v": 1, "event": e.event, "task_id": str(task_id), "ts": e.ts.isoformat(), "seq": e.seq,
                 "data": _sign_fields(e.data)} for e in rows]


@router.websocket("/ws/tasks/{task_id}")
async def task_socket(ws: WebSocket, task_id: uuid.UUID, ticket: str = Query(max_length=64),
                      since: int = Query(default=0, ge=0)) -> None:
    origin = ws.headers.get("origin")
    if origin and origin not in get_settings().cors_origin_list:
        await ws.close(code=4403)
        return
    owner = get_redis().getdel(f"wsticket:{ticket}")  # single use
    if not owner or owner.split("|")[1] != str(task_id):
        await ws.close(code=4401)
        return
    user_id = uuid.UUID(owner.split("|")[0])
    await ws.accept()

    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(channel(str(task_id)))  # subscribe BEFORE reading the backlog: no gap
    try:
        last = since
        for env in await asyncio.to_thread(_backlog, task_id, since):
            await ws.send_json(env)
            last = env["seq"]

        async def pump() -> None:
            nonlocal last
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                env = json.loads(msg["data"])
                if env["seq"] <= last:
                    continue  # already sent from the backlog
                env["data"] = _sign_fields(env.get("data"))
                await ws.send_json(env)
                last = env["seq"]

        async def commands() -> None:
            while True:
                raw = await ws.receive_text()
                if len(raw) > MAX_CLIENT_MSG:
                    await ws.send_json({"event": "error", "data": {"message": "message too large"}})
                    continue
                await _handle_command(ws, user_id, task_id, raw)

        async def keepalive() -> None:
            while True:
                await asyncio.sleep(25)
                await ws.send_json({"event": "ping"})

        tasks = [asyncio.create_task(c()) for c in (pump, commands, keepalive)]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            if t.exception() and not isinstance(t.exception(), WebSocketDisconnect):
                log.warning("ws.error", error=str(t.exception())[:200])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
        await r.aclose()


async def _handle_command(ws: WebSocket, user_id: uuid.UUID, task_id: uuid.UUID, raw: str) -> None:
    try:
        msg = json.loads(raw)
    except ValueError:
        await ws.send_json({"event": "error", "data": {"message": "invalid JSON"}})
        return
    kind = msg.get("type")
    if kind not in {"approve", "cancel"}:
        await ws.send_json({"event": "error", "data": {"message": "unknown command"}})
        return
    try:
        limit(f"ws-cmd:{user_id}", 30, 60)

        def run() -> dict[str, Any]:
            with session_scope() as db:
                user = db.get(User, user_id)
                if kind == "approve":
                    return submit_decision(db, user, task_id, ApproveIn.model_validate(msg.get("data") or {}))
                from ease.api.routes.tasks import cancel_task

                return {"status": cancel_task(task_id, user, db)["status"]}

        result = await asyncio.to_thread(run)
        await ws.send_json({"event": "command.ok", "data": {"type": kind, **json.loads(json.dumps(result,
                                                                                               default=str))}})
    except (HTTPException, ValidationError) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else "invalid command payload"
        await ws.send_json({"event": "command.error", "data": {"type": kind, "message": detail}})


# ---------------- artifact files ----------------
@router.get("/files/{uri:path}")
def artifact_file(uri: str, exp: int = Query(...), sig: str = Query(max_length=64)) -> FileResponse:
    if not verify_artifact(uri, exp, sig):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "link expired or invalid")
    root = get_settings().artifacts_dir.resolve()
    path = (root / uri).resolve()
    if root not in path.parents or not path.is_file():  # path traversal guard
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    media = {".png": "image/png", ".json": "application/json", ".html": "text/plain"}.get(path.suffix,
                                                                                          "application/octet-stream")
    # HTML dumps are served as text/plain so captured pages can never execute in our origin.
    return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=600",
                                                         "Content-Security-Policy": "default-src 'none'"})

