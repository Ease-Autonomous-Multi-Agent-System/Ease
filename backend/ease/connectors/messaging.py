"""Messaging connectors: Telegram (bot API) and Slack (incoming webhook).

Both are write operations: the planner marks them MEDIUM risk, and every message is sent at most once per step
(idempotency key), so a retried step or a resumed run never sends the same message twice.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ease.connectors.base import Connector, ConnectorContext, ConnectorError, Operation, idempotency_key

SLACK_WEBHOOK_PREFIX = "https://hooks.slack.com/"


class TelegramMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000, description="the message to send (plain text)")
    parse_mode: Literal["HTML", "MarkdownV2"] | None = None


class TelegramConnector(Connector):
    service = "telegram"
    auth_type = "api_key"
    base_url = "https://api.telegram.org"
    credential_ref = "telegram:default"
    operations = {
        "send_message": Operation(
            "send_message",
            "Send a Telegram message to the user (e.g. a summary of results).",
            TelegramMessage,
            writes=True,
            output_hint="{sent: bool, message_id: int}",
        )
    }

    def op_send_message(self, q: TelegramMessage, ctx: ConnectorContext) -> dict[str, Any]:
        # The token is decrypted here and passed straight into the request URL - never stored or logged.
        token = ctx.secret(self.credential_ref)
        chat_id = ctx.secret("telegram:chat_id")
        if not token or not chat_id:
            raise ConnectorError("Telegram is not connected - add a bot token and your chat id", auth=True)
        if not ctx.idempotency(idempotency_key("telegram", chat_id, ctx.step_key, q.text)):
            return {"sent": False, "skipped_duplicate": True}
        body: dict[str, Any] = {"chat_id": chat_id, "text": q.text}
        if q.parse_mode:
            body["parse_mode"] = q.parse_mode
        data = self.request("POST", f"{self.base_url}/bot{token}/sendMessage", json=body).json()
        if not data.get("ok", False):
            raise ConnectorError(f"telegram: {str(data.get('description', 'message was not sent'))[:200]}")
        return {"sent": True, "message_id": (data.get("result") or {}).get("message_id")}


class SlackMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000, description="the message to post")


class SlackConnector(Connector):
    service = "slack"
    auth_type = "webhook"
    base_url = "https://hooks.slack.com"
    credential_ref = "slack:default"
    operations = {
        "post_message": Operation(
            "post_message",
            "Post a message to the user's Slack channel via their incoming webhook.",
            SlackMessage,
            writes=True,
            output_hint="{sent: bool}",
        )
    }

    def op_post_message(self, q: SlackMessage, ctx: ConnectorContext) -> dict[str, Any]:
        url = ctx.secret(self.credential_ref)
        if not url:
            raise ConnectorError("Slack is not connected - add an incoming webhook URL", auth=True)
        # Security: only ever post to Slack. A wrong or malicious value must not send data anywhere else.
        if not url.startswith(SLACK_WEBHOOK_PREFIX):
            raise ConnectorError("the Slack webhook must start with https://hooks.slack.com/", auth=True)
        if not ctx.idempotency(idempotency_key("slack", url, ctx.step_key, q.text)):
            return {"sent": False, "skipped_duplicate": True}
        self.request("POST", url, json={"text": q.text})
        return {"sent": True}
