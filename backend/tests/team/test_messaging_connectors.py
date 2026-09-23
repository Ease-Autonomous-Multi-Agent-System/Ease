"""Acceptance tests for the Telegram and Slack connectors (team task - see docs/team-tasks.md, task 1).

They are skipped until ease/connectors/messaging.py exists. When the connectors are implemented, every test
here must pass without changing this file. No network: HTTP is intercepted with httpx.MockTransport.
"""

import json

import httpx
import pytest

messaging = pytest.importorskip("ease.connectors.messaging")

from ease.connectors.base import ConnectorContext, ConnectorError  # noqa: E402


def ctx(secrets: dict[str, str], seen: set | None = None):
    seen = seen if seen is not None else set()

    def idem(key: str) -> bool:
        if key in seen:
            return False
        seen.add(key)
        return True

    return ConnectorContext(user_id="u", task_id="t", step_key="notify", secret=secrets.get, idempotency=idem)


# ---------------- Telegram ----------------
def test_telegram_sends_message():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    conn = messaging.TelegramConnector(transport=httpx.MockTransport(handler))
    out = conn.call("send_message", {"text": "3 new internships found"},
                    ctx({"telegram:default": "123456:ABCdefGhIJKlmNoPQRstuVWxyz0123456789", "telegram:chat_id": "987"}))
    assert out["sent"] is True and out["message_id"] == 42
    assert calls[0].url.path.endswith("/sendMessage")
    body = json.loads(calls[0].content)
    assert body["chat_id"] == "987" and body["text"] == "3 new internships found"


def test_telegram_is_idempotent_on_retry():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    conn = messaging.TelegramConnector(transport=httpx.MockTransport(handler))
    seen: set = set()
    secrets = {"telegram:default": "123456:ABCdefGhIJKlmNoPQRstuVWxyz0123456789", "telegram:chat_id": "1"}
    conn.call("send_message", {"text": "hello"}, ctx(secrets, seen))
    out = conn.call("send_message", {"text": "hello"}, ctx(secrets, seen))  # same step retried
    assert n["calls"] == 1 and out["sent"] is False and out.get("skipped_duplicate") is True


def test_telegram_requires_credentials():
    conn = messaging.TelegramConnector(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(ConnectorError):
        conn.call("send_message", {"text": "x"}, ctx({}))


def test_telegram_rejects_oversized_text():
    conn = messaging.TelegramConnector(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(Exception):
        conn.call("send_message", {"text": "x" * 5000},
                  ctx({"telegram:default": "1:abc", "telegram:chat_id": "1"}))


def test_telegram_is_a_write_operation():
    assert messaging.TelegramConnector.operations["send_message"].writes is True
    assert messaging.TelegramConnector.credential_ref == "telegram:default"


# ---------------- Slack ----------------
def test_slack_posts_to_webhook():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, text="ok")

    conn = messaging.SlackConnector(transport=httpx.MockTransport(handler))
    hook = "https://hooks.slack.com/services/T000/B000/XXXXXXXX"
    out = conn.call("post_message", {"text": "Run finished"}, ctx({"slack:default": hook}))
    assert out["sent"] is True
    assert str(calls[0].url) == hook and json.loads(calls[0].content)["text"] == "Run finished"


def test_slack_refuses_non_slack_webhook_url():
    conn = messaging.SlackConnector(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")))
    with pytest.raises(ConnectorError):
        conn.call("post_message", {"text": "x"}, ctx({"slack:default": "https://evil.example/collect"}))


def test_connectors_are_registered():
    from ease.connectors.registry import CONNECTOR_CLASSES

    names = {c.service for c in CONNECTOR_CLASSES}
    assert {"telegram", "slack"} <= names
