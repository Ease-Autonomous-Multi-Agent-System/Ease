# Ease

**Supervised-autonomy multi-agent system for web navigation and task automation.**
Final-year major project — Department of Computer Science, ABES Engineering College, Ghaziabad.

You type a goal in plain English. A planner LLM turns it into a typed plan (a DAG of steps). Each step runs through
the cheapest reliable route — a REST API when one exists, otherwise a real browser. Anything irreversible pauses for
your approval, and that pause survives worker crashes and restarts.

```
"Find remote ML internships, rank them against my resume, and apply to the best one"
        │
        ▼
  planner ──► jobs (API or browser) ──► rank vs resume (embeddings) ──► fill form ──► ⏸ you approve ──► submit
```

## What makes it different

| | |
|---|---|
| **Hybrid execution** | Per step, the router picks an API connector (≈0.3 s) or the browser agent (≈20–60 s). |
| **Hybrid grounding** | The browser agent acts on a numbered DOM / accessibility-tree index; a Set-of-Marks screenshot is added only when the index is ambiguous. |
| **Durable human-in-the-loop** | `interrupt()` + Postgres checkpoints. The worker is released while you decide; any worker resumes later. The approved form is replayed in a fresh browser from a recorded action script, with your edits applied, then submitted. |
| **Safety decided by code, not by the model** | Irreversible clicks are intercepted, risk levels have a code-enforced floor, navigation is pinned to the task's site, secrets never reach prompts, logs or checkpoints. |
| **Seller trust ratings** | Search and price results are rated *trusted / unverified / not trusted* by rules in code (known retailers and official brand stores, throw-away or look-alike domains, brand impersonation, prices far below the rest). The answer only recommends trusted sellers. |
| **Follow-up questions** | Ask about a finished run; the planner reuses its results (`$previous.items`) and only searches again when it needs new information. |
| **Runs on free tiers** | An LLM router falls back across Groq → Gemini → OpenRouter → GitHub Models → Ollama, with response caching and hard budgets. Embeddings run locally. |

## Architecture

| Layer | Tech |
|---|---|
| Presentation | Next.js (after the UI design session) — the API and WebSocket contracts are final |
| API gateway | FastAPI: JWT auth with refresh rotation, rate limits, 202-and-enqueue task intake, WebSocket hub |
| Async bus | Redis (broker, pub/sub, budgets, locks) + Celery workers (`agent` queue with Chromium, `io` queue) + Beat |
| Orchestration | LangGraph state machine: supervisor → dispatch → {api, browser, extraction} agents → aggregator, with recovery cycles and the approval gate |
| Agents | Playwright browser agent · httpx connectors (arXiv, OpenAlex, Greenhouse, Lever, Notion, Sheets) · extraction/matching (fastembed + pgvector) |
| Persistence | PostgreSQL 16 + pgvector, LangGraph `PostgresSaver`, AES-256-GCM envelope-encrypted vault |

```
backend/ease/
  api/            FastAPI app, routes (auth, tasks, resources, ws), deps
  graph/          planner, capability manifest, state machine, runners, stores
  agents/         browser/ (index, Set-of-Marks, session, loop), extraction, documents
  connectors/     connector framework + services
  llm/            free-tier router + cache
  security/       vault, auth, rate limits & LLM budgets, SSRF guard
  worker/         Celery app + tasks
  eval/           benchmark, harness, report
fixtures/         deterministic local test sites (job board, shop, member portal)
docs/             team tasks, design notes
```

## Quick start

Requirements: Docker Desktop (8 GB memory), Python 3.12, and free API keys for Gemini
([aistudio.google.com](https://aistudio.google.com)) and Groq ([console.groq.com](https://console.groq.com)).

```bash
cp .env.example .env          # then paste your keys; generate secrets as described in the file
docker compose --profile web up -d --build   # web :3000, api :8000, fixtures :8080 (all on 127.0.0.1)
```

- The app: http://localhost:3000 (create an account on the sign-up page)
- API docs: http://127.0.0.1:8000/docs
- Fixture sites: http://127.0.0.1:8080
- Queue dashboard: `docker compose --profile ops up -d flower` → http://127.0.0.1:5555

### Local development (no web stack)

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e "backend[browser,ml,dev,eval]"
.venv/Scripts/python -m playwright install chromium
docker compose up -d postgres redis fixtures
cd backend
../.venv/Scripts/python -m ease.graph.run --prompt "Get the 5 most recent cs.AI papers from arXiv"
```

For frontend work, leave out `--profile web` and run `npm --prefix frontend run dev` instead (hot reload, same port).

Put your resume at `private/resume.pdf` (git-ignored) to use matching and form filling.

## Tests

```bash
cd backend
../.venv/Scripts/python -m pytest -q                                # unit: state machine, security, router, contracts
../.venv/Scripts/python -m pytest -q -m integration tests/integration  # API against real Postgres + Redis
python scripts/e2e_hitl_restart.py                                  # M3: pause, kill worker, approve, finish
```

CI (GitHub Actions) runs lint, migrations up/down/up, unit and integration tests, and a gitleaks secret scan on every push.

## Evaluation

```bash
python -m ease.eval.harness --suite all --repeats 3        # fixture and live suites, separately
python -m ease.eval.harness --suite all --conditions grounding   # DOM vs vision vs hybrid
python -m ease.eval.harness --suite fixture --conditions hitl    # approvals on vs off
python -m ease.eval.harness --suite all --conditions planner     # hierarchical vs single prompt
python -m ease.eval.report                                  # tables + charts for the report
```

Every failed run is labelled (`BOT_WALL`, `GROUNDING_MISS`, `PLAN_INVALID`, `PLAN_WRONG`, `AUTH_EXPIRED`,
`TIMEOUT`, `TOOL_ERROR`, `ESCALATED`), and environment failures are reported separately from agent failures.

## Security

- **Secrets:** AES-256-GCM envelope encryption with a per-record data key and associated data that binds each ciphertext
  to its (user, service) row. Credentials are write-only through the API, decrypted only inside a tool call, and
  redacted from logs by pattern and by value.
- **Auth:** bcrypt; HS256 JWTs with pinned algorithm; single-use rotating refresh tokens; optional invite code for sign-up.
- **Abuse / cost:** per-IP and per-email login limits, per-user task and concurrency limits, and atomic LLM budgets
  per task / user / day / deployment, checked before every model call.
- **Agent safety:** SSRF guard on every browser and connector request; navigation pinned to the task's site;
  robots.txt respected; no typing into password/payment/OTP fields; irreversible actions need approval;
  page text treated as untrusted data; seller trust decided by code, which web content cannot raise.
- **API:** strict CORS, security headers, body-size limit, 404 for other users' resources, single-use WebSocket
  tickets with origin checks, short-lived signed artifact URLs, upload type detection by content.
- **Repo:** `.env` and `private/` are git-ignored; gitleaks runs in pre-commit and CI.

## Scope and ethics

In scope: career and document workflows up to (not including) unapproved submission; research aggregation.
Out of scope: payments, CAPTCHA/2FA bypass, account creation, mass crawling. The agent prepares, the human submits.

## Team

Varun Narayan Singh · Uddesh Pratap Singh · Utkarsh Dubey — guided by Dr. Anshika Agarwal. See
[docs/team-tasks.md](docs/team-tasks.md) and the step-by-step [docs/TEAM-GUIDE.md](docs/TEAM-GUIDE.md).

## Literature

The papers this work builds on (WebVoyager, CowPilot, Agentic Lybic, ColorBrowserAgent, WebUncertainty, Autonoma,
AsyncFlow, the 2025 AI Agent Index) are summarised in [docs/literature.md](docs/literature.md).
