# Team tasks

Small, self-contained pieces of Ease that each team member owns end to end. Each has a clear spec, a working
example to follow, and acceptance tests that already exist — you're done when they pass.

Workflow for every task:

1. `git checkout -b feat/<your-initials>-<task>` (never commit to `main` directly)
2. Build it, run the tests locally (commands below)
3. Open a pull request to `main`; CI must be green (lint, tests, secret scan)
4. Be ready to explain your piece in the viva: what it does, how it's tested, what could go wrong

Setup (once): see "Local development" in the README. Never put API keys in code — only in your own `.env`.

---

## Task 1 — Telegram and Slack connectors (Uddesh)

**Why it matters:** lets a workflow notify the user ("3 new internships matched your resume") — the
"multi-channel logging" part of the project scope. The planner starts using them automatically once they are
registered.

**What to build:** `backend/ease/connectors/messaging.py` with two classes.

| Class | service | auth | operation | inputs | output |
|---|---|---|---|---|---|
| `TelegramConnector` | `telegram` | bot token (`telegram:default`) + chat id (`telegram:chat_id`) | `send_message` | `text` (1–4000 chars), optional `parse_mode` | `{sent, message_id}` |
| `SlackConnector` | `slack` | incoming-webhook URL (`slack:default`) | `post_message` | `text` (1–4000 chars) | `{sent}` |

Follow `backend/ease/connectors/productivity.py` (`NotionConnector`) exactly:

- inputs are a Pydantic model; the operation is declared in `operations = {...}` with `writes=True`
- read secrets only via `ctx.secret(...)` inside the operation, pass them straight to the request, never store or log them
- use `self.request(...)` (it already does SSRF checks, retries and backoff)
- idempotency: `if not ctx.idempotency(idempotency_key("telegram", chat_id, ctx.step_key, text)): return {"sent": False, "skipped_duplicate": True}`
- Telegram API: `POST https://api.telegram.org/bot<token>/sendMessage` with JSON `{chat_id, text}`
- Slack: `POST <webhook url>` with JSON `{text}`; refuse any URL that doesn't start with `https://hooks.slack.com/` (raise `ConnectorError`)
- register both classes in `backend/ease/connectors/registry.py`
- add `"telegram:chat_id"` handling to the server-level fallback in `backend/ease/graph/store_db.py` → `DbStore.secret` (read `TELEGRAM_CHAT_ID` from settings), next to the existing Telegram token fallback

**Done when:** `cd backend && ../.venv/Scripts/python -m pytest tests/team -q` passes all 8 tests, and a real
message arrives in your Telegram chat when you run:

```
python -m ease.graph.run --prompt "Get the 3 newest cs.AI papers from arXiv and send their titles to me on Telegram"
```

Getting a bot: message @BotFather on Telegram → `/newbot` → copy the token. Send any message to your bot, then open
`https://api.telegram.org/bot<token>/getUpdates` to find your chat id.

---

## Task 2 — Two more fixture pages + benchmark cases (Utkarsh)

**Why it matters:** the fixture suite is where every grounding and recovery number in the report is measured. These
two pages test the recovery loop (popup) and multi-page extraction (pagination).

**What to build** (plain HTML + JS, no frameworks — copy the style of `fixtures/jobs/index.html`):

1. `fixtures/popup/index.html` — a news/articles list (8–10 fixed articles with title, author, date). After ~1
   second an intrusive modal ("Subscribe to our newsletter!") covers the page and blocks clicks until it's closed.
   The close button is an `×` icon **with no text label** (so the text index alone can't name it — the vision
   fallback has to). The modal must come back once if the user scrolls to the bottom.
2. `fixtures/list/index.html` — a numbered list of 45 fixed "courses" (code, title, credits, instructor) shown
   10 per page with Previous / 1 2 3 4 5 / Next buttons, rendered by JS (not separate HTML files).

Then add benchmark cases to `backend/ease/eval/benchmark.json` (copy the format of `F01`..`F11`):

- `F12-popup-articles`: "On http://fixtures:8080/popup/ list the titles of all articles by <an author you chose>" — check `titles_include`
- `F13-popup-latest`: "What is the newest article on http://fixtures:8080/popup/ ?" — check `text_contains`
- `F14-list-page4`: "On http://fixtures:8080/list/ list every course taught by <instructor> (check all pages)" — pick an instructor whose courses are spread across pages 1, 3 and 5
- `F15-list-credits`: "How many 4-credit courses are on http://fixtures:8080/list/ ?" — check `text_contains`

**Done when:** both pages work in a browser at `http://127.0.0.1:8080/popup/` and `/list/`, and

```
python -m ease.eval.harness --cases F12-popup-articles,F13-popup-latest,F14-list-page4,F15-list-credits
```

runs all four (they don't all have to pass — record what happens; failures are results too).

---

## Task 3 — Evaluation runs and results chapter (both)

After tasks 1–2: run the full benchmark (`--suite all --repeats 3`) and the three ablations
(`--conditions grounding`, `--conditions hitl`, `--conditions planner`), then `python -m ease.eval.report`.
Write the Results chapter from the generated `report.md` and charts: success rates, the fixture-vs-live gap,
the failure distribution, human cost and API-vs-browser latency. The free LLM tiers limit how many runs fit in a
day, so spread the runs over a week.
