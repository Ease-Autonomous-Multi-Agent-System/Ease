# Ease

**Supervised-autonomy multi-agent system for web navigation and task automation.**
Final-year major project — Department of Computer Science, ABES Engineering College, Ghaziabad.

You type a goal in plain English. A planner LLM turns it into a typed plan (a DAG of steps). Each step runs through
the cheapest reliable route — a REST API when one exists, otherwise a real browser. Anything irreversible pauses for
your approval, and that pause survives worker crashes and restarts.

**To run it on your own computer, see [Run it on your computer](#run-it-on-your-computer)** (Docker, about 20 minutes).

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

## Run it on your computer

Everything runs in Docker on your own machine: the website, API, workers, database and the demo test sites. It is
all free. Only the AI calls go to the internet, using free Groq / Gemini keys.

### What you need

| | |
|---|---|
| **Docker Desktop** | https://www.docker.com/products/docker-desktop (Windows: keep the default WSL 2 option). Give it at least **6 GB of memory** (Settings → Resources) and 15 GB of free disk. |
| **Git** | https://git-scm.com/downloads |
| **Python 3.10+** | https://www.python.org/downloads (only used once, to create the settings file) |
| **Free AI keys** | **Groq**: https://console.groq.com/keys (fast text model), and **Gemini**: https://aistudio.google.com/apikey (needed for screenshots). No card needed. |

### 1. Get the code

```bash
git clone https://github.com/Ease-Autonomous-Multi-Agent-System/Ease.git
cd Ease
```

### 2. Create your settings file

```bash
python scripts/setup_env.py
```

This creates `.env` with fresh random secrets (the database password and the keys that encrypt saved API keys).
`.env` is git-ignored and never committed.

Then **add your AI keys**, either way:

- open `.env` in any editor and fill in `GROQ_API_KEY=` and `GEMINI_API_KEY=`, **or**
- skip this for now, and after signing in add them in the app under **Profile & apps → AI model keys**.

### 3. Start Ease

Make sure Docker Desktop is running, then:

```bash
docker compose --profile web up -d --build
```

The **first start takes 10–20 minutes**: it downloads images, builds the website and installs Chromium. Later starts
take about 30 seconds. Check that everything is up with:

```bash
docker compose --profile web ps
```

Every service listed should say `Up` (and `healthy` where shown).

### 4. Open it

Go to **http://localhost:3000**:

1. Choose **Create an account**. It's local to your machine; leave the invite code empty.
2. If you didn't put keys in `.env`, go to **Profile & apps → AI model keys** and add them.
3. Optionally, upload your resume under **Profile & apps**. It's needed for form filling and job matching.
4. Go to **New task** and click an example, e.g. **Research digest** or **Browse a catalogue**, then **Start**.
   Watch the plan and the agent's browser live. When a step would submit something, approve it in the side panel.

The in-app **Guide** (top bar) and the **About** page explain every feature.

### Everyday commands

| To… | Run |
|---|---|
| stop Ease (your data is kept) | `docker compose --profile web down` |
| start it again | `docker compose --profile web up -d` |
| update to the latest code | `git pull` then `docker compose --profile web up -d --build` |
| see what it is doing | `docker compose logs -f api worker-agent` |
| wipe everything and start fresh (deletes accounts and runs) | `docker compose --profile web down -v` |

### Other local addresses

- Demo test sites the agent practises on: http://127.0.0.1:8080
- API docs: http://127.0.0.1:8000/docs
- Queue dashboard: `docker compose --profile ops up -d flower`, then http://127.0.0.1:5555

All ports are bound to `127.0.0.1`, so nothing is reachable from other devices on your Wi-Fi.

### Troubleshooting

| Problem | Fix |
|---|---|
| `docker: command not found` / "cannot connect to the Docker daemon" | Start Docker Desktop and wait until it says *Engine running*. |
| "port is already allocated" (3000, 8000, 5432, 6379 or 8080) | Another program uses that port. Stop it, or stop another copy of Ease with `docker compose --profile web down`. |
| The build stops with a network or timeout error | Run the same `up -d --build` command again. It continues where it stopped. |
| Tasks fail with "no AI key" or "add your own Groq or Gemini API key" | Add a key in `.env` (then run `docker compose --profile web up -d`), or in the app under Profile & apps. |
| Tasks pause with a rate-limit message | The free AI tiers allow limited requests per minute and day. Wait a minute and run the task again. |
| A red "Console Error … hydration" box in the browser | A browser extension (e.g. an antivirus toolbar) changed the page. Use a private window, or turn the extension off for localhost. |
| Anything else | `docker compose logs --tail 100 api worker-agent` shows the error. |

### For developers

**Frontend with hot reload.** Run the backend in Docker without the web profile, then the website with npm:

```bash
docker compose up -d --build
npm --prefix frontend ci
npm --prefix frontend run dev          # http://localhost:3000
```

**Command-line runner (no website).** Useful for trying the agents directly:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e "backend[browser,ml,dev,eval]"   # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m playwright install chromium
docker compose up -d postgres redis fixtures
cd backend
../.venv/Scripts/python -m ease.graph.run --prompt "Get the 5 most recent cs.AI papers from arXiv"
```

Put a resume at `private/resume.pdf` (git-ignored) to use matching and form filling from the command line.

**Deploying publicly** is on hold for now. Tested setups are ready for an AWS EC2 server
([docs/DEPLOY-AWS.md](docs/DEPLOY-AWS.md)) and a Hugging Face Space ([docs/DEPLOY.md](docs/DEPLOY.md)).

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

## Using Ease

The app has two public pages: **About** (`/about`: the project in detail) and **Getting started** (`/guide`:
account setup). In short:

1. **Sign up** at `/register` (with the invite code if the server sets `REGISTRATION_INVITE_CODE`).
2. **Profile & apps** (optional):
   - Upload a resume, which fills your profile, and check the fields the agent will type into forms.
   - Paste free keys for the services you want: Tavily (web search), Serper (price comparison), Notion, Telegram,
     Slack, or Google Sheets. Keys are encrypted and write-only.
3. **New task:** describe the goal in plain English. Watch the plan, the agent's view and the event log live.
4. **Approve:** irreversible steps pause with the filled form. Edit any field, then approve or skip.
5. **Results:** read the answer and the table (price results carry seller trust badges), then ask a follow-up.

## Future work

- **Vision grounding:** a stronger or fine-tuned vision model for icon-only and canvas-heavy pages.
- **Skill memory:** reusable per-site action sequences learned from successful runs, repaired when a layout changes.
- **Scheduled and triggered workflows:** e.g. "every Monday, new ML internships to Telegram".
- **More connectors:** Gmail and Calendar (OAuth), LinkedIn, GitHub, college portals, so more steps use APIs
  instead of the browser.
- **Hosted multi-user deployment:** per-user browser sandboxes, OAuth sign-in, quotas, and optional paid models.
- **Voice input and mobile approvals** from a push notification.
- **Larger evaluation:** WebVoyager / Mind2Web-style live tasks, multiple models, and a user study of time saved.
- **Prompt-injection classifier:** screens page text before the agent reads it, alongside today's rule-based flag.

## Scope and ethics

In scope: research, price comparison, browsing and extraction, and form workflows up to (not including)
unapproved submission.
Out of scope: payments, CAPTCHA/2FA bypass, account creation, mass crawling. The agent prepares, the human submits.

## Team

Varun Narayan Singh · Uddesh Pratap Singh · Utkarsh Dubey — guided by Dr. Anshika Agarwal. See
[docs/team-tasks.md](docs/team-tasks.md) and the step-by-step [docs/TEAM-GUIDE.md](docs/TEAM-GUIDE.md).

## Literature

The papers this work builds on (WebVoyager, CowPilot, Agentic Lybic, ColorBrowserAgent, WebUncertainty, Autonoma,
AsyncFlow, the 2025 AI Agent Index) are summarised in [docs/literature.md](docs/literature.md).
