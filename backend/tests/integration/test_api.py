"""API integration tests against real Postgres + Redis (docker compose up -d postgres redis).

Run:  pytest -m integration
Celery enqueueing is intercepted so no worker (and no LLM) is involved.
"""

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytestmark = pytest.mark.integration

PW = "correct-horse-42"


@pytest.fixture(scope="module")
def client():
    from ease.api.main import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def reset_rate_limits():
    from ease.security.ratelimit import get_redis

    r = get_redis()
    for key in r.scan_iter("rl:*"):
        r.delete(key)


@pytest.fixture(autouse=True)
def server_ai_key(monkeypatch):
    """Tasks need an AI key to be accepted; give the server a (never used) one."""
    from ease.config import get_settings

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_used")
    monkeypatch.setenv("USER_KEYS_ONLY", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_celery(monkeypatch):
    sent = []
    from ease.worker import tasks as wt

    monkeypatch.setattr(wt.run_workflow, "apply_async", lambda *a, **k: sent.append(("run", a, k)))
    monkeypatch.setattr(wt.resume_workflow, "apply_async", lambda *a, **k: sent.append(("resume", a, k)))
    monkeypatch.setattr(wt.ingest_document, "delay", lambda *a, **k: sent.append(("ingest", a, k)))
    return sent


def _register(client, email=None):
    email = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": PW, "full_name": "Test"})
    assert r.status_code == 201, r.text
    return email, {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()


def test_security_headers_and_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert client.get("/ready").json() == {"postgres": "ok", "redis": "ok"}


def test_register_login_refresh_rotation(client):
    weak = {"email": f"w-{uuid.uuid4().hex[:6]}@example.com", "password": "weakpassword"}
    r = client.post("/auth/register", json=weak)
    assert r.status_code == 422  # letters only
    email, headers, tokens = _register(client)
    assert client.post("/auth/register", json={"email": email, "password": PW}).status_code == 409
    assert client.post("/auth/login", json={"email": email, "password": "wrong-password-1"}).status_code == 401
    r = client.post("/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200
    refresh = r.json()["refresh_token"]
    r2 = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 200
    # refresh tokens are single-use
    assert client.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 401
    assert client.get("/me", headers=headers).json()["email"] == email
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer " + refresh}).status_code == 401  # wrong type


def test_login_brute_force_is_rate_limited(client):
    email, _, _ = _register(client)
    codes = [client.post("/auth/login", json={"email": email, "password": f"nope-{i}-xxxxx"}).status_code
             for i in range(8)]
    assert 429 in codes


def test_task_lifecycle_and_idor(client, no_celery):
    _, alice, _ = _register(client)
    _, mallory, _ = _register(client)
    r = client.post("/tasks", json={"prompt": "Get the top 5 recent cs.AI papers"}, headers=alice)
    assert r.status_code == 202
    tid = r.json()["task_id"]
    assert no_celery[-1][0] == "run"
    assert client.get(f"/tasks/{tid}", headers=alice).json()["status"] == "QUEUED"
    # another user cannot see, cancel or approve it - and learns nothing (404, not 403)
    assert client.get(f"/tasks/{tid}", headers=mallory).status_code == 404
    assert client.post(f"/tasks/{tid}/cancel", headers=mallory).status_code == 404
    assert client.get(f"/tasks/{tid}/events", headers=mallory).status_code == 404
    assert [t["id"] for t in client.get("/tasks", headers=mallory).json()] == []
    assert client.post("/tasks", json={"prompt": "x" * 2001}, headers=alice).status_code == 422
    assert client.post("/tasks", json={"prompt": "ok", "config": {"evil": 1}}, headers=alice).status_code == 422
    r = client.post(f"/tasks/{tid}/cancel", headers=alice)
    assert r.json()["status"] == "CANCELLED"


def test_concurrent_task_limit(client):
    _, h, _ = _register(client)
    codes = [client.post("/tasks", json={"prompt": f"task number {i}"}, headers=h).status_code for i in range(3)]
    assert codes[:2] == [202, 202] and codes[2] == 429


def test_vault_is_write_only_and_encrypted(client):
    from ease.db.models import Credential
    from ease.db.session import session_scope

    _, h, _ = _register(client)
    secret = "ntn_" + "S3cretValue" * 4
    r = client.put("/credentials/notion/default", json={"secret": secret}, headers=h)
    assert r.status_code == 200 and secret not in r.text and r.json()["hint"].endswith(secret[-4:])
    listing = client.get("/credentials", headers=h).text
    assert secret not in listing
    with session_scope() as s:
        rows = s.scalars(select(Credential).where(Credential.hint == r.json()["hint"])).all()
        assert rows and all(secret.encode() not in (c.ciphertext + c.wrapped_dek) for c in rows)
    assert client.put("/credentials/aws/root", json={"secret": "x"}, headers=h).status_code == 422
    assert client.delete("/credentials/notion/default", headers=h).status_code == 204


def test_upload_validation(client, no_celery):
    _, h, _ = _register(client)
    r = client.post("/documents", files={"file": ("cv.pdf", b"\x00\x01binary-not-pdf\xff\xfe", "application/pdf")},
                    headers=h)
    assert r.status_code == 415  # content sniffing, not the claimed name/MIME
    big = b"%PDF" + b"0" * (5 * 1024 * 1024 + 10)
    assert client.post("/documents", files={"file": ("big.pdf", big, "application/pdf")}, headers=h).status_code == 413
    r = client.post("/documents", files={"file": ("../../etc/notes.txt", b"hello resume text", "text/plain")},
                    headers=h)
    assert r.status_code == 202 and "/" not in r.json()["filename"]
    assert no_celery[-1][0] == "ingest"


def test_approval_is_single_use_and_field_checked(client, no_celery):
    from ease.db.models import Approval, Task, TaskStatus
    from ease.db.session import session_scope

    _, h, _ = _register(client)
    tid = client.post("/tasks", json={"prompt": "apply for a job"}, headers=h).json()["task_id"]
    aid = uuid.uuid4()
    with session_scope() as s:
        s.get(Task, uuid.UUID(tid)).status = TaskStatus.AWAITING_APPROVAL
        s.add(Approval(id=aid, task_id=uuid.UUID(tid), step_key="apply",
                       payload_json={"reason": "submit?", "kind": "commit",
                                     "fields": [{"locator": "#email", "label": "Email", "value": "a@b.co"}]}))
    body = {"approval_id": str(aid), "decision": "approve", "edited_fields": {"#password": "x"}}
    assert client.post(f"/tasks/{tid}/approve", json=body, headers=h).status_code == 422
    body["edited_fields"] = {"#email": "new@b.co"}
    assert client.get(f"/tasks/{tid}", headers=h).json()["pending_approval"]["approval_id"] == str(aid)
    r1 = client.post(f"/tasks/{tid}/approve", json=body, headers=h).json()
    r2 = client.post(f"/tasks/{tid}/approve", json=body, headers=h).json()
    assert r1["status"] == "accepted" and r2["status"] == "already_decided"
    assert [c for c in no_celery if c[0] == "resume"].__len__() == 1


def test_signed_files(client, tmp_path):
    from ease.api.deps import sign_artifact
    from ease.config import get_settings

    root = get_settings().artifacts_dir
    (root / "t-sign").mkdir(parents=True, exist_ok=True)
    (root / "t-sign" / "a.png").write_bytes(b"\x89PNG fake")
    url = sign_artifact("t-sign/a.png")
    assert client.get(url).status_code == 200
    assert client.get(url.replace("sig=", "sig=0")).status_code == 403
    assert client.get("/files/t-sign/a.png?exp=9999999999&sig=deadbeef").status_code == 403
    evil = sign_artifact("../.env")
    assert client.get(evil).status_code == 404  # signed but outside the artifacts root
    expired = f"/files/t-sign/a.png?exp={int(time.time()) - 5}&sig=x"
    assert client.get(expired).status_code == 403


def test_websocket_ticket_backlog_and_origin(client):
    from ease.events.emitter import EventEmitter

    _, h, _ = _register(client)
    tid = client.post("/tasks", json={"prompt": "hello world task"}, headers=h).json()["task_id"]
    EventEmitter().emit(tid, "task.status", {"status": "PLANNING"})
    ticket = client.post("/ws-ticket", json={"task_id": tid}, headers=h).json()["ticket"]
    with client.websocket_connect(f"/ws/tasks/{tid}?ticket={ticket}") as sock:
        first = sock.receive_json()
        assert first["event"] == "task.status" and first["seq"] == 1
    # ticket is single use
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/tasks/{tid}?ticket={ticket}") as sock:
            sock.receive_json()
    t2 = client.post("/ws-ticket", json={"task_id": tid}, headers=h).json()["ticket"]
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/tasks/{tid}?ticket={t2}", headers={"origin": "https://evil.example"}) as s:
            s.receive_json()


def test_follow_up_carries_previous_result_and_is_owner_only(client, no_celery):
    from ease.db.models import StepState, Task, TaskStatus, TaskStep
    from ease.db.session import session_scope

    _, alice, _ = _register(client)
    _, mallory, _ = _register(client)
    tid = client.post("/tasks", json={"prompt": "price of casio f-91w"}, headers=alice).json()["task_id"]
    body = {"prompt": "which one has the best rating?", "follow_up_of": tid}
    assert client.post("/tasks", json=body, headers=alice).status_code == 409  # still running
    with session_scope() as s:
        s.get(Task, uuid.UUID(tid)).status = TaskStatus.COMPLETED
        s.add(TaskStep(task_id=uuid.UUID(tid), step_key="prices", agent_kind="api", tool="api.shopping.search",
                       status=StepState.DONE, output_json={"items": [{"store": "Flipkart", "trust": "trusted"}]}))
    assert client.post("/tasks", json=body, headers=mallory).status_code == 404
    r = client.post("/tasks", json=body, headers=alice)
    assert r.status_code == 202
    with session_scope() as s:
        cfg = s.get(Task, uuid.UUID(r.json()["task_id"])).config_json
    assert cfg["follow_up_of"] == tid and cfg["previous"]["items"][0]["store"] == "Flipkart"
    # the stored context is not echoed back to the browser
    assert "previous" not in client.get(f"/tasks/{r.json()['task_id']}", headers=alice).json()["config"]


def test_admin_password_reset(client, monkeypatch):
    from ease import admin

    email, _, _ = _register(client)
    new_pw = "brand-new-pass-77"
    monkeypatch.setattr(admin.getpass, "getpass", lambda prompt="": new_pw)
    assert admin.reset_password(email.upper()) == 0  # emails are matched case-insensitively
    assert client.post("/auth/login", json={"email": email, "password": PW}).status_code == 401
    assert client.post("/auth/login", json={"email": email, "password": new_pw}).status_code == 200
    assert admin.reset_password("nobody@example.com") == 1


def test_public_site_runs_only_on_the_users_own_ai_key(client, no_celery, monkeypatch):
    from ease.config import get_settings

    monkeypatch.setenv("USER_KEYS_ONLY", "true")  # the server's GROQ_API_KEY must now be ignored
    get_settings.cache_clear()
    _, h, _ = _register(client)
    me = client.get("/me", headers=h).json()
    assert me["ai"] == {"own_ai_key": False, "own_vision_key": False, "ai_ready": False, "user_keys_only": True}
    r = client.post("/tasks", json={"prompt": "Get the 5 newest cs.AI papers"}, headers=h)
    assert r.status_code == 409 and "API key" in r.json()["detail"]

    assert client.put("/credentials/groq/default", json={"secret": "gsk_user_own_key_123", "kind": "api_key"},
                      headers=h).status_code in (200, 201)
    assert client.get("/me", headers=h).json()["ai"]["ai_ready"] is True
    assert client.post("/tasks", json={"prompt": "Get the 5 newest cs.AI papers"}, headers=h).status_code == 202


def test_router_uses_the_run_owners_key_and_never_the_servers(monkeypatch):
    import httpx

    from ease.config import get_settings
    from ease.llm.router import LlmRouter, LlmUnavailable

    monkeypatch.setenv("USER_KEYS_ONLY", "true")
    get_settings.cache_clear()
    seen = []

    def handler(req):
        seen.append(req.headers["authorization"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}], "usage": {}})

    keys = {("alice", "groq"): "gsk_alice"}
    r = LlmRouter(transport=httpx.MockTransport(handler), key_lookup=lambda u, p: keys.get((u, p)))
    r.cache.mode = "off"
    r.complete([{"role": "user", "content": "x"}], user_id="alice")
    assert seen == ["Bearer gsk_alice"]
    with pytest.raises(LlmUnavailable, match="add your own"):
        r.complete([{"role": "user", "content": "y"}], user_id="bob")
