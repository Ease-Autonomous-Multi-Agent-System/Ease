"""Credentials (vault), documents, connectors registry, saved workflow templates."""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ease.api.deps import current_user, limit
from ease.config import get_settings
from ease.connectors.base import ConnectorContext
from ease.connectors.registry import registry
from ease.db.models import AuditLog, Credential, CredentialKind, Document, User, WorkflowTemplate
from ease.db.session import get_db
from ease.schemas.api import CredentialIn, CredentialOut, DocumentOut, TemplateIn
from ease.security.vault import Vault

router = APIRouter(tags=["resources"])

# service -> allowed credential names. "cookies" names are hostnames (session cookies for the browser agent).
_SERVICES = {"notion": {"default", "database_id"}, "telegram": {"default", "chat_id"}, "slack": {"default"},
             "google": {"service_account"}}
_HOST = re.compile(r"^[a-z0-9.\-]{1,100}$")


def _check_name(service: str, name: str) -> None:
    if service == "cookies":
        if not _HOST.match(name):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "cookie credentials are named by hostname")
        return
    if service not in _SERVICES or name not in _SERVICES[service]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unsupported credential {service}:{name}")


# ---------------- credentials ----------------
@router.get("/credentials", response_model=list[CredentialOut])
def list_credentials(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[CredentialOut]:
    rows = db.scalars(select(Credential).where(Credential.user_id == user.id).order_by(Credential.service))
    # Write-only: only the masked hint ever leaves the server.
    return [CredentialOut(service=c.service, name=c.name, kind=c.kind.value, hint=c.hint, created_at=c.created_at)
            for c in rows]


@router.put("/credentials/{service}/{name}", response_model=CredentialOut)
def put_credential(service: str, name: str, body: CredentialIn, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> CredentialOut:
    limit(f"vault:{user.id}", 20, 60)
    _check_name(service, name)
    if service == "cookies":
        import json

        try:
            cookies = json.loads(body.secret)
            assert isinstance(cookies, list) and all(isinstance(c, dict) and "name" in c for c in cookies)
        except (ValueError, AssertionError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "cookies must be a JSON list of {name, value, domain, path}") from exc
    row = Vault(db).put(user.id, service, name, body.secret.strip(), CredentialKind(body.kind))
    return CredentialOut(service=row.service, name=row.name, kind=row.kind.value, hint=row.hint,
                         created_at=row.created_at)


@router.delete("/credentials/{service}/{name}", status_code=204)
def delete_credential(service: str, name: str, user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> None:
    if not Vault(db).delete(user.id, f"{service}:{name}"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "credential not found")


# ---------------- documents ----------------
@router.post("/documents", response_model=DocumentOut, status_code=202)
async def upload_document(file: UploadFile = File(...), doc_type: str = Form(default="resume"),
                          user: User = Depends(current_user), db: Session = Depends(get_db)) -> DocumentOut:
    limit(f"upload:{user.id}", 10, 3600)
    from ease.agents.documents import UnsupportedDocument, sniff_type

    if doc_type not in {"resume", "cover_letter", "notes"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "doc_type must be resume, cover_letter or notes")
    max_bytes = get_settings().max_upload_bytes
    data = await file.read(max_bytes + 1)  # never read more than the limit into memory
    if len(data) > max_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"file larger than {max_bytes // 1_000_000} MB")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "empty file")
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", (file.filename or "upload"))[:120]
    try:
        kind = sniff_type(data, name)
    except UnsupportedDocument as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    folder = get_settings().uploads_dir / str(user.id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}.{'pdf' if kind == 'pdf' else 'txt'}"  # never trust the client filename
    path.write_bytes(data)
    doc = Document(user_id=user.id, doc_type=doc_type, filename=name, storage_path=str(path),
                   sha256=hashlib.sha256(data).hexdigest())
    db.add(doc)
    db.add(AuditLog(user_id=user.id, action="document.upload", resource=name))
    db.commit()
    from ease.worker.tasks import ingest_document

    ingest_document.delay(str(doc.id))
    return DocumentOut(id=doc.id, doc_type=doc.doc_type, filename=doc.filename, parse_status=doc.parse_status.value,
                       created_at=doc.created_at, chars=0)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[DocumentOut]:
    rows = db.scalars(select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc()))
    return [DocumentOut(id=d.id, doc_type=d.doc_type, filename=d.filename, parse_status=d.parse_status.value,
                        created_at=d.created_at, chars=len(d.raw_text or "")) for d in rows]


@router.delete("/documents/{doc_id}", status_code=204)
def delete_document(doc_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    doc = db.scalar(select(Document).where(Document.id == doc_id, Document.user_id == user.id))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    from pathlib import Path

    Path(doc.storage_path).unlink(missing_ok=True)
    db.delete(doc)


# ---------------- connectors ----------------
@router.get("/connectors")
def list_connectors(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    from ease.graph.store_db import DbStore

    store = DbStore()
    ctx = ConnectorContext(user_id=str(user.id), task_id=None, step_key=None,
                           secret=lambda ref: store.secret(str(user.id), ref))
    out = []
    for svc, conn in registry().items():
        out.append({
            "service": svc, "auth_type": conn.auth_type, "configured": conn.configured(ctx),
            "operations": [{"name": o.name, "description": o.description, "writes": o.writes}
                           for o in conn.operations.values()],
        })
    return out


# ---------------- workflow templates ----------------
@router.get("/templates")
def list_templates(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(select(WorkflowTemplate).where(WorkflowTemplate.user_id == user.id)
                      .order_by(WorkflowTemplate.created_at.desc()))
    return [{"id": t.id, "name": t.name, "prompt": t.prompt, "schedule": t.schedule_cron, "enabled": t.enabled,
             "last_run_at": t.last_run_at} for t in rows]


@router.post("/templates", status_code=201)
def create_template(body: TemplateIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    count = len(list(db.scalars(select(WorkflowTemplate.id).where(WorkflowTemplate.user_id == user.id))))
    if count >= 25:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "template limit reached (25)")
    t = WorkflowTemplate(user_id=user.id, name=body.name, prompt=body.prompt, schedule_cron=body.schedule)
    db.add(t)
    db.flush()
    return {"id": t.id, "name": t.name, "prompt": t.prompt, "schedule": t.schedule_cron}


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(template_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    t = db.scalar(select(WorkflowTemplate).where(WorkflowTemplate.id == template_id,
                                                 WorkflowTemplate.user_id == user.id))
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found")
    db.delete(t)
