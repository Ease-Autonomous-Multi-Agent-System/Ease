# Team guide — exactly what to do and what to push

For **Uddesh** (task 1) and **Utkarsh** (task 2). Follow it top to bottom; every command is written for Windows
(run them in **Git Bash**, which comes with Git for Windows). If something doesn't match what you see, stop and ask
in the group — don't guess.

- Repo: https://github.com/Ease-Autonomous-Multi-Agent-System/Ease
- Your issue: Uddesh → [#1](https://github.com/Ease-Autonomous-Multi-Agent-System/Ease/issues/1), Utkarsh → [#2](https://github.com/Ease-Autonomous-Multi-Agent-System/Ease/issues/2)
- The golden rule: **you never push to `main`**. You push *your own branch* and open a *pull request* (PR).
  Varun reviews and merges it.

---

## Part 0 — one-time setup (both of you, ~20 minutes)

### 0.1 Install
- Git for Windows: https://git-scm.com/download/win
- Python **3.12**: https://www.python.org/downloads/ (tick "Add python.exe to PATH" in the installer)
- VS Code (any editor is fine)

### 0.2 Accept the GitHub invite
Open https://github.com/Ease-Autonomous-Multi-Agent-System and accept the organisation invite if GitHub shows one.
You must be able to open the repo link above before continuing.

### 0.3 Get the code
```bash
cd ~/Desktop
git clone https://github.com/Ease-Autonomous-Multi-Agent-System/Ease.git
cd Ease
git config user.name  "Your Name"
git config user.email "the-email-on-your-github-account@example.com"
```

### 0.4 Python environment
```bash
python -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip
.venv/Scripts/python -m pip install -e "backend[browser,dev]"
.venv/Scripts/python -m playwright install chromium        # Utkarsh needs this; Uddesh can skip it
```
Check it works:
```bash
cd backend
../.venv/Scripts/python -m pytest -q
cd ..
```
You should see something like `56 passed, 1 skipped`. If you see red errors, send a screenshot to the group.

### 0.5 Your own `.env` (your own free API keys)
```bash
cp .env.example .env
```
Open `.env` in VS Code and fill **only** these lines (keys are free, no card):
- `GEMINI_API_KEY=` → from https://aistudio.google.com → "Get API key"
- `GROQ_API_KEY=` → from https://console.groq.com → "API Keys"
- `JWT_SECRET=` → run `python -c "import secrets; print(secrets.token_urlsafe(48))"` and paste the output
- `VAULT_MASTER_KEY=` → run `python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"` and paste the output

`.env` is ignored by git on purpose. **Never** paste keys into code, chat, or a commit.

### 0.6 Safety net (blocks you from committing a key by accident)
```bash
.venv/Scripts/python -m pip install pre-commit
.venv/Scripts/pre-commit install
```

---

## Part 1 — the everyday workflow (same for every task)

```bash
git checkout main
git pull                                   # always start from the latest code
git checkout -b feat/<your-initials>-<task>   # e.g. feat/ups-telegram  or  feat/ud-fixtures
# ... do the work ...
git status                                 # check WHICH files changed - see the "never commit" list below
git add <the files you changed>            # add files by name, not "git add ." blindly
git commit -m "Add Telegram connector"     # short, plain message
git push -u origin feat/<your-initials>-<task>
```
Then open the repo on GitHub → it shows a yellow bar "Compare & pull request" → click it → in the description write
`Closes #1` (or `#2`) → **Create pull request**. Wait for the checks to go green (2–3 minutes). If they're red,
open the failing check, read the error, fix it, commit and push again to the *same branch* — the PR updates itself.

### Never commit these
| Path | Why |
|---|---|
| `.env` | your API keys |
| `private/` | personal files (resumes) |
| `data/` | screenshots, caches, eval results from your runs |
| `.venv/`, `node_modules/` | installed packages |

They're already in `.gitignore`, so `git status` shouldn't even list them. If it does, ask before committing.

---

## Part 2 — Uddesh: Telegram and Slack connectors

### What you push (exactly 3 files)
| File | Change |
|---|---|
| `backend/ease/connectors/messaging.py` | **new** — the two connectors (starter below) |
| `backend/ease/connectors/registry.py` | add 2 lines: import + register both classes |
| `backend/ease/graph/store_db.py` | add the `telegram:chat_id` fallback (1–2 lines) |

The tests already exist in `backend/tests/team/test_messaging_connectors.py` — **don't edit them**. You're done when
they pass.

### Step 1 — read the example first (10 minutes)
Open `backend/ease/connectors/productivity.py` and read `NotionConnector` top to bottom. Your code follows the same
pattern: an input model, an `operations` dict, and an `op_<name>` method.

### Step 2 — create `backend/ease/connectors/messaging.py` from this starter
Fill in the parts marked `TODO`.

```python
"""Messaging connectors: Telegram (bot API) and Slack (incoming webhook)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ease.connectors.base import Connector, ConnectorContext, ConnectorError, Operation, idempotency_key


class TelegramMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    parse_mode: str | None = None


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
        token = ctx.secret(self.credential_ref)
        chat_id = ctx.secret("telegram:chat_id")
        # TODO 1: if token or chat_id is missing -> raise ConnectorError("...", auth=True)
        # TODO 2: idempotency - if not ctx.idempotency(idempotency_key("telegram", chat_id, ctx.step_key, q.text)):
        #         return {"sent": False, "skipped_duplicate": True}
        # TODO 3: call self.request("POST", f"{self.base_url}/bot{token}/sendMessage",
        #         json={"chat_id": chat_id, "text": q.text})   (add parse_mode only if it was given)
        # TODO 4: return {"sent": True, "message_id": <result.message_id from the JSON response>}
        raise NotImplementedError


class SlackMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


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
        # TODO 1: missing url -> ConnectorError(..., auth=True)
        # TODO 2: SECURITY - refuse any url that does not start with "https://hooks.slack.com/"
        #         (raise ConnectorError). Otherwise a bad value could send data anywhere.
        # TODO 3: idempotency, same idea as Telegram
        # TODO 4: self.request("POST", url, json={"text": q.text}); return {"sent": True}
        raise NotImplementedError
```

Rules (the tests check them): never `print` or log the token; never store it on `self`; only use it inside the
method, passed straight into the request.

### Step 3 — register them
In `backend/ease/connectors/registry.py`:
```python
from ease.connectors.messaging import SlackConnector, TelegramConnector
```
and add `TelegramConnector, SlackConnector,` to `CONNECTOR_CLASSES` (replace the comment line that mentions them).

### Step 4 — the chat-id fallback
In `backend/ease/graph/store_db.py`, method `DbStore.secret`, next to the existing
`"telegram:default": st.telegram_bot_token,` line, handle `"telegram:chat_id"` by returning
`st.telegram_chat_id` (it's a plain string setting, not a secret, so return it directly if set).

### Step 5 — test
```bash
cd backend
../.venv/Scripts/python -m pytest tests/team -q        # must show 8 passed
../.venv/Scripts/python -m pytest -q                   # everything else must still pass
../.venv/Scripts/python -m ruff check ease tests       # must say "All checks passed!"
cd ..
```

### Step 6 — try it for real (optional but great for the viva)
1. In Telegram, message **@BotFather** → `/newbot` → copy the token.
2. Send any message to your new bot, then open `https://api.telegram.org/bot<token>/getUpdates` in a browser →
   find `"chat":{"id": 123456789` → that's your chat id.
3. Put both in **your** `.env`: `TELEGRAM_BOT_TOKEN=...` and `TELEGRAM_CHAT_ID=...`
4. Run:
   ```bash
   cd backend
   ../.venv/Scripts/python -m ease.graph.run --prompt "Get the 3 newest cs.AI papers from arXiv and send their titles to me on Telegram"
   ```
   A message should arrive on your phone. Take a screenshot for the report.

### Step 7 — push
```bash
git status        # should list exactly your 3 files
git add backend/ease/connectors/messaging.py backend/ease/connectors/registry.py backend/ease/graph/store_db.py
git commit -m "Add Telegram and Slack connectors"
git push -u origin feat/ups-messaging
```
Open the PR with `Closes #1`.

---

## Part 3 — Utkarsh: popup and paginated-list test pages + benchmark cases

### What you push (exactly 3 files)
| File | Change |
|---|---|
| `fixtures/popup/index.html` | **new** — article list behind an annoying newsletter popup |
| `fixtures/list/index.html` | **new** — 45 courses, 10 per page, with page buttons |
| `backend/ease/eval/benchmark.json` | add 4 cases: `F12`–`F15` |

### Step 1 — run the existing test sites to see the style
```bash
.venv/Scripts/python -m http.server 8080 -d fixtures
```
Open http://127.0.0.1:8080/jobs/ and http://127.0.0.1:8080/shop/ in your browser. Keep this terminal running while
you work; press Ctrl+C to stop it. Open `fixtures/jobs/index.html` in VS Code — your pages copy its structure:
`<link rel="stylesheet" href="/common.css">`, a header bar, data defined in a JS array, rendered by a small script.

### Step 2 — `fixtures/popup/index.html`
Must have:
- A header "The Daily Byte" and a list of **9 fixed articles** in a JS array: `title`, `author`, `date`
  (`YYYY-MM-DD`). Use 3 authors, 3 articles each.
- After **1 second**, a newsletter modal covers the page (a full-screen semi-transparent layer plus a white box with
  an email input and a "Subscribe" button). While it's open, clicks on the articles must not work (the overlay sits
  on top).
- The close button is **only an `×` character with no text label, no `aria-label` and no `title`** — this is on
  purpose: it's the case the agent's screenshot fallback has to solve.
- After it's closed, it comes back **once** when the user scrolls to the bottom of the page.
- No external scripts, no frameworks, no images from the internet.

### Step 3 — `fixtures/list/index.html`
Must have:
- A JS array of **45 fixed courses**: `code` (e.g. `CS101`), `title`, `credits` (2, 3 or 4), `instructor`
  (use 5 instructors).
- Pick one instructor (e.g. "Dr. Rao") whose courses are on **pages 1, 3 and 5** only.
- Show **10 per page** with buttons: `Previous`, `1 2 3 4 5`, `Next`. Clicking re-renders the list with JS (same page,
  no separate HTML files) — copy the `render()` and `go()` idea from `fixtures/jobs/index.html`.
- Count how many 4-credit courses you made — you'll need the number in step 4.

### Step 4 — add the benchmark cases
Open `backend/ease/eval/benchmark.json`. After the `F11-portal-no-session` case, add four entries in the same format
(watch the commas!):

```json
{"id": "F12-popup-articles", "suite": "fixture", "domain": "general",
 "prompt": "On http://fixtures:8080/popup/ list the titles of all articles by <AUTHOR YOU CHOSE>.",
 "checks": [{"type": "titles_include", "field": "title", "expected": ["<title 1>", "<title 2>", "<title 3>"], "min_recall": 1.0}]},
{"id": "F13-popup-latest", "suite": "fixture", "domain": "general",
 "prompt": "What is the newest article on http://fixtures:8080/popup/ ?",
 "checks": [{"type": "text_contains", "any_of": ["<exact title of the newest article>"]}]},
{"id": "F14-list-instructor", "suite": "fixture", "domain": "general",
 "prompt": "On http://fixtures:8080/list/ list every course taught by <INSTRUCTOR> (check all pages).",
 "checks": [{"type": "titles_include", "field": "title", "expected": ["<course title>", "<course title>", "<course title>"], "min_recall": 1.0}]},
{"id": "F15-list-credits", "suite": "fixture", "domain": "general",
 "prompt": "How many 4-credit courses are on http://fixtures:8080/list/ ?",
 "checks": [{"type": "text_contains", "any_of": ["<the number>"]}]}
```
Replace every `<...>` with the real values from your pages.

### Step 5 — run your cases
In a **second** Git Bash window (keep the http.server one running):
```bash
cd backend
../.venv/Scripts/python -m ease.eval.harness --cases F12-popup-articles,F13-popup-latest,F14-list-instructor,F15-list-credits
```
Each case prints `PASS` or `FAIL[LABEL]`. **Failing is fine** — these pages are meant to be hard. Copy the printed
results into your PR description. Also check the JSON is valid:
```bash
../.venv/Scripts/python -c "import json; json.load(open('ease/eval/benchmark.json')); print('json ok')"
../.venv/Scripts/python -m pytest -q
cd ..
```

### Step 6 — push
```bash
git status        # should list exactly your 3 files (the eval results in data/ are ignored - don't add them)
git add fixtures/popup/index.html fixtures/list/index.html backend/ease/eval/benchmark.json
git commit -m "Add popup and paginated-list fixture pages with benchmark cases"
git push -u origin feat/ud-fixtures
```
Open the PR with `Closes #2`.

---

## Part 4 — after both PRs are merged (issue #3, both of you)

Run the full benchmark in batches (the free LLM limits allow ~30 tasks a day each — use your own keys and split the
work), then generate the report:
```bash
cd backend
../.venv/Scripts/python -m ease.eval.harness --suite fixture --repeats 3
../.venv/Scripts/python -m ease.eval.harness --suite live --repeats 3
../.venv/Scripts/python -m ease.eval.report
```
Results land in `data/eval/` on your machine. Share the `report.md` and PNG charts in the group (don't commit
`data/`); they go into the Results chapter.

---

## When stuck

| Problem | Fix |
|---|---|
| `git push` says "permission denied" | you haven't accepted the org invite (Part 0.2) |
| pre-commit blocks the commit with "leaks found" | you're committing a key — remove it from the file, keep it only in `.env` |
| PR check "secret scan" red | same as above; tell Varun, the key must be rotated |
| PR check "backend" red | open it, scroll to the red step, read the last lines; usually a test or ruff error you can reproduce locally |
| `git pull` says "conflict" | don't panic, don't force-push; send a screenshot to the group |
| `ModuleNotFoundError` | you're not using `../.venv/Scripts/python` — use the full path as in the commands above |
