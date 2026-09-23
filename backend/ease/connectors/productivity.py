"""Write connectors: Notion (log rows to a database) and Google Sheets (append rows via a service account).

Writes are idempotent: each row gets a key derived from (task, step, row content) and is skipped if it was
already written - so a retried step or a resumed graph never creates duplicates.
"""

from __future__ import annotations

import json
import time
from typing import Any

import jwt
from pydantic import BaseModel, Field

from ease.config import get_settings
from ease.connectors.base import Connector, ConnectorContext, ConnectorError, Operation, idempotency_key

NOTION_VERSION = "2022-06-28"


def _cell(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else str(v)


class NotionRows(BaseModel):
    items: list[dict[str, Any]] = Field(min_length=1, max_length=25)
    title_field: str = Field(default="title", description="which item field becomes the page title")
    database_id: str | None = Field(default=None, description="omit to use the user's default database")


class NotionConnector(Connector):
    service = "notion"
    auth_type = "api_key"
    base_url = "https://api.notion.com/v1"
    credential_ref = "notion:default"
    operations = {
        "create_rows": Operation(
            "create_rows",
            "Log items as rows (pages) in the user's Notion database. Each item's fields are mapped to database "
            "columns with the same name; anything else goes into the page body.",
            NotionRows,
            writes=True,
            output_hint="{created: int, skipped_duplicates: int, page_urls: [str]}",
        )
    }

    def _headers(self, ctx: ConnectorContext) -> dict[str, str]:
        token = ctx.secret(self.credential_ref)  # decrypted here, handed straight to the header, never stored
        if not token:
            raise ConnectorError("Notion is not connected - add a Notion token in the vault", auth=True)
        return {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}

    def op_create_rows(self, q: NotionRows, ctx: ConnectorContext) -> dict[str, Any]:
        db = q.database_id or ctx.secret("notion:database_id") or get_settings().notion_database_id
        if not db:
            raise ConnectorError("no Notion database id configured")
        headers = self._headers(ctx)
        schema = self.request("GET", f"{self.base_url}/databases/{db}", headers=headers).json()["properties"]
        title_prop = next(name for name, p in schema.items() if p["type"] == "title")
        by_lower = {name.lower(): (name, p["type"]) for name, p in schema.items()}

        created, skipped, urls = 0, 0, []
        for item in q.items:
            if not ctx.idempotency(idempotency_key("notion", db, ctx.step_key, item)):
                skipped += 1
                continue
            props: dict[str, Any] = {
                title_prop: {"title": [{"text": {"content": _cell(item.get(q.title_field) or "Untitled")[:200]}}]}
            }
            leftovers = []
            for k, v in item.items():
                if k == q.title_field:
                    continue
                match = by_lower.get(k.lower())
                if match and match[1] == "rich_text":
                    props[match[0]] = {"rich_text": [{"text": {"content": _cell(v)[:1900]}}]}
                elif match and match[1] == "url" and isinstance(v, str) and v.startswith("http"):
                    props[match[0]] = {"url": v}
                elif match and match[1] == "number" and isinstance(v, int | float):
                    props[match[0]] = {"number": v}
                else:
                    leftovers.append(f"{k}: {_cell(v)}")
            children = [
                {"object": "block", "type": "paragraph",
                 "paragraph": {"rich_text": [{"text": {"content": line[:1900]}}]}}
                for line in leftovers[:20]
            ]
            page = self.request(
                "POST", f"{self.base_url}/pages", headers=headers,
                json={"parent": {"database_id": db}, "properties": props, "children": children},
            ).json()
            created += 1
            urls.append(page.get("url", ""))
        return {"created": created, "skipped_duplicates": skipped, "page_urls": urls}


class SheetRows(BaseModel):
    items: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    columns: list[str] | None = Field(default=None, description="column order; defaults to the first item's keys")
    sheet_range: str = Field(default="Sheet1!A1", pattern=r"^[\w .\-']{1,60}![A-Z]{1,3}\d{0,6}$")
    spreadsheet_id: str | None = None


class SheetsConnector(Connector):
    service = "sheets"
    auth_type = "service_account"
    base_url = "https://sheets.googleapis.com/v4/spreadsheets"
    credential_ref = "google:service_account"
    operations = {
        "append_rows": Operation(
            "append_rows",
            "Append items as rows to a Google Sheet (shared with the service account).",
            SheetRows,
            writes=True,
            output_hint="{appended: int, skipped_duplicates: int}",
        )
    }

    def _token(self, ctx: ConnectorContext) -> str:
        raw = ctx.secret(self.credential_ref)
        if not raw:
            raise ConnectorError("Google service account is not configured", auth=True)
        sa = json.loads(raw)
        now = int(time.time())
        assertion = jwt.encode(
            {"iss": sa["client_email"], "scope": "https://www.googleapis.com/auth/spreadsheets",
             "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 600},
            sa["private_key"], algorithm="RS256",
        )
        r = self.request("POST", "https://oauth2.googleapis.com/token",
                         data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
        return r.json()["access_token"]

    def op_append_rows(self, q: SheetRows, ctx: ConnectorContext) -> dict[str, Any]:
        sheet = q.spreadsheet_id or get_settings().google_sheet_id
        if not sheet:
            raise ConnectorError("no Google Sheet id configured")
        cols = q.columns or list(q.items[0].keys())
        fresh = [i for i in q.items if ctx.idempotency(idempotency_key("sheets", sheet, ctx.step_key, i))]
        if fresh:
            token = self._token(ctx)
            self.request(
                "POST", f"{self.base_url}/{sheet}/values/{q.sheet_range}:append?valueInputOption=USER_ENTERED",
                headers={"Authorization": f"Bearer {token}"},
                json={"values": [[_cell(i.get(c))[:5000] for c in cols] for i in fresh]},
            )
        return {"appended": len(fresh), "skipped_duplicates": len(q.items) - len(fresh)}
