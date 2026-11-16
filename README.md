# Ease

**A supervised-autonomy multi-agent system for web navigation and task automation.**
Final-year major project, Department of Computer Science, ABES Engineering College, Ghaziabad.

You type a goal in plain English. A planner LLM turns it into a typed plan (a DAG of steps). Each step runs through
the cheapest reliable route: a REST API when one exists, otherwise a real browser. Anything irreversible pauses for
your approval, and that pause survives worker crashes and restarts.

```
"Find remote ML internships, rank them against my resume, and apply to the best one"
        │
        ▼
  planner ──► jobs (API or browser) ──► rank vs resume (embeddings) ──► fill form ──► ⏸ you approve ──► submit
```

**Quick start:** install Docker Desktop, then run

```bash
git clone https://github.com/Ease-Autonomous-Multi-Agent-System/Ease.git
cd Ease
python scripts/setup_env.py
docker compose --profile web up -d --build
```

and open **http://localhost:3000**. The full step-by-step guide is in [Run it locally](#run-it-locally).

## Contents

- [What makes it different](#what-makes-it-different)
- [Architecture](#architecture)
- [Run it locally (step by step)](#run-it-locally)
- [Demo walkthrough](#demo-walkthrough)
- [Everyday commands](#everyday-commands)
- [Troubleshooting](#troubleshooting)
- [Developer setup (without Docker for the website or agents)](#developer-setup)
- [Tests](#tests)
- [Reproducing the evaluation](#reproducing-the-evaluation)
- [Security](#security) · [Scope and ethics](#scope-and-ethics) · [Future work](#future-work) · [Team](#team)

## What makes it different

| | |
|---|---|
| **Hybrid execution** | Per step, the router picks an API connector (≈0.3 s) or the browser agent (≈20–60 s). |
| **Hybrid grounding** | The browser agent acts on a numbered DOM / accessibility-tree index; a Set-of-Marks screenshot is added only when the index is ambiguous. |
| **Durable human-in-the-loop** | `interrupt()` + Postgres checkpoints. The worker is released while you decide; any worker resumes later. The approved form is replayed in a fresh browser from a recorded action script, with your edits applied, then submitted. |
| **Safety decided by code, not by the model** | Irreversible clicks are intercepted, risk levels have a code-enforced floor, navigation is pinned to the task's site, banking / payment / crypto / government-identity sites are never opened, and secrets never reach prompts, logs or checkpoints. |
| **Website sign-in on your behalf** | When a site needs a login, the password is typed by code on that host only, never shown to the AI, and deleted after the run unless you ask Ease to remember it. |
| **Seller trust ratings** | Search and price results are rated *trusted / unverified / not trusted* by rules in code (known retailers and official brand stores, throw-away or look-alike domains, brand impersonation, prices far below the rest). The answer only recommends trusted sellers. |
| **Follow-up questions** | Ask about a finished run; the planner reuses its results (`$previous.items`) and only searches again when it needs new information. |
| **Runs on free tiers** | An LLM router falls back across Groq → Gemini → OpenRouter → GitHub Models → Ollama, with response caching and hard budgets. Embeddings run locally. |

## Architecture

| Layer | Tech |
|---|---|
| Presentation | Next.js 16 + React: live plan timeline, agent's browser view, event log, approval side panel |
| API gateway | FastAPI: JWT auth with refresh rotation, rate limits, 202-and-enqueue task intake, WebSocket hub |
| Async bus | Redis (broker, pub/sub, budgets, locks) + Celery workers (`agent` queue with Chromium, `io` queue) + Beat |
| Orchestration | LangGraph state machine: supervisor → dispatch → {api, browser, extraction} agents → aggregator, with recovery cycles and the approval gate |
| Agents | Playwright browser agent · httpx connectors (arXiv, OpenAlex, Greenhouse, Lever, Tavily, Serper, Notion, Sheets, Telegram, Slack) · extraction/matching (fastembed + pgvector) |
| Persistence | PostgreSQL 16 + pgvector, LangGraph `PostgresSaver`, AES-256-GCM envelope-encrypted vault |

```
backend/ease/
  api/            FastAPI app, routes (auth, tasks, resources, ws), deps
  graph/          planner, capability manifest, state machine, runners, stores
  agents/         browser/ (index, Set-of-Marks, session, loop), extraction, documents
  connectors/     connector framework + services, seller trust rules
  llm/            free-tier router + cache
  security/       vault, auth, rate limits & LLM budgets, SSRF / blocked-site guard, website logins
  worker/         Celery app + tasks
  eval/           benchmark, harness, report, paper metrics, external-agent baselines
backend/scripts/  end-to-end HITL restart test, workload scaling
frontend/         Next.js web app
fixtures/         deterministic local test sites (job board, shop, member portal, list, pop-up)
deploy/           gateway, AWS and Hugging Face deployment files
docs/             deployment guides, team guide, literature notes
```

<a id="run-it-locally"></a>
## Run it locally (step by step)

Everything runs in Docker on your own machine: the website, API, workers, database and the demo test sites. It is
all free. Only the AI calls go to the internet, using free Groq / Gemini keys.

### Step 0 — Install the prerequisites

| | |
|---|---|
| **Docker Desktop** | https://www.docker.com/products/docker-desktop (Windows: keep the default WSL 2 option). In **Settings → Resources** give it at least **6 GB of memory**. Keep about 15 GB of disk free. |
| **Git** | https://git-scm.com/downloads |
| **Python 3** | https://www.python.org/downloads (any recent version; only used once to create the settings file). |
| **Free AI keys** | **Groq**: https://console.groq.com/keys (fast text model) and **Gemini**: https://aistudio.google.com/apikey (needed for screenshots). No card required. |

Check that the tools work (open a terminal: PowerShell on Windows, Terminal on macOS/Linux):

```bash
docker --version
git --version
python --version
```

On macOS/Linux use `python3` wherever this guide says `python`.

### Step 1 — Get the code

```bash
git clone https://github.com/Ease-Autonomous-Multi-Agent-System/Ease.git
cd Ease
```

### Step 2 — Create the settings file

```bash
python scripts/setup_env.py
```

This creates `.env` from `.env.example` with fresh random secrets (database password, login-token secret, and the key
that encrypts saved API keys). `.env` is git-ignored and never committed. Running it again never overwrites an
existing `.env`.

### Step 3 — Add your AI keys

Choose one:

- **In the file:** open `.env` in any text editor and fill in
  ```
  GROQ_API_KEY=gsk_...
  GEMINI_API_KEY=AIza...
  ```
- **In the app:** skip this now, and after signing in add them under **Profile & apps → AI model keys**.

### Step 4 — Start Ease

Make sure Docker Desktop is open and shows *Engine running*, then:

```bash
docker compose --profile web up -d --build
```

The **first start takes 10–20 minutes**: it downloads images, builds the website and installs Chromium. Later starts
take about 30 seconds. The database tables are created automatically by the `migrate` service.

### Step 5 — Check that everything is running

```bash
docker compose --profile web ps
```

`postgres`, `redis`, `fixtures`, `api`, `worker-agent`, `worker-io`, `beat`, `web` and `gateway` should say `Up`
(and `healthy` where shown). The one-off `migrate` service creates the database tables and then exits, so it is
not listed; `docker compose --profile web ps -a` shows it as `Exited (0)`.

### Step 6 — Open the app and sign up

1. Go to **http://localhost:3000**.
2. Choose **Create an account**. The account exists only on your machine; leave the invite code empty.
3. If you skipped Step 3, open **Profile & apps → AI model keys** and add your Groq and Gemini keys.
4. Optional: upload a resume under **Profile & apps**. It is used for form filling and job matching.

### Step 7 — Run your first task

Open **New task**, click an example such as **Research digest** or **Browse a catalogue**, then **Start**. You will
see the plan, the agent's live browser and the event log. When a step would submit something, an approval panel
slides in: edit any field, then approve or skip.

The in-app **Guide** (top bar) and the **About** page explain every feature.

### Local addresses

| What | Address |
|---|---|
| Web app | http://localhost:3000 |
| API docs (Swagger) | http://127.0.0.1:8000/docs |
| Demo test sites the agent practises on | http://127.0.0.1:8080 |
| Queue dashboard (optional) | `docker compose --profile ops up -d flower`, then http://127.0.0.1:5555 |

All ports are bound to `127.0.0.1`, so nothing is reachable from other devices on your Wi-Fi.

## Demo walkthrough

A five-minute live demo. Steps 1–3 use the local test sites, so only the AI calls need the internet:

1. **Cheapest item (browser agent):** `What is the cheapest phone on http://fixtures:8080/shop/ ?`
2. **Extraction from a job board:** `On http://fixtures:8080/jobs/ list all remote internships with their company and location.`
3. **Form filling + approval (HITL):** upload a resume first, then
   `Fill the application form for the Computer Vision Intern job at http://fixtures:8080/jobs/apply.html?id=cv-intern-02 using my profile.`
   The run pauses before **Submit**; show the filled form in the approval panel, edit a field, approve.
4. **Research via API (no browser):** `Get the 5 most recent cs.AI papers from arXiv` (fast API route).
5. **Follow-up:** on the finished run, ask `Which of these is about agents?` and show that it reuses the results.
6. **Safety:** try `Open https://www.hdfcbank.com and check my balance` and show that the agent refuses to open it.

Tip: start the stack and run one task before the presentation, so images are built and caches are warm.

## Everyday commands

| To… | Run |
|---|---|
| stop Ease (your data is kept) | `docker compose --profile web down` |
| start it again | `docker compose --profile web up -d` |
| update to the latest code | `git pull` then `docker compose --profile web up -d --build` |
| see what it is doing | `docker compose logs -f api worker-agent` |
| wipe everything and start fresh (deletes accounts and runs) | `docker compose --profile web down -v` |
| list accounts | `docker compose exec api python -m ease.admin list-users` |
| reset a forgotten password | `docker compose exec api python -m ease.admin reset-password you@example.com` (asks for the new one, hidden) |
| clear sign-in rate limits | `docker compose exec api python -m ease.admin clear-rate-limits` |

## Troubleshooting

| Problem | Fix |
|---|---|
| `docker: command not found` / "cannot connect to the Docker daemon" | Start Docker Desktop and wait until it says *Engine running*. |
| "port is already allocated" (3000, 8000, 5432, 6379 or 8080) | Another program uses that port. Stop it, or stop another copy of Ease with `docker compose --profile web down`. |
| The build stops with a network or timeout error | Run the same `up -d --build` command again. It continues where it stopped. |
| A container keeps restarting or the build is killed | Give Docker more memory (Settings → Resources, at least 6 GB). |
| Tasks fail with "no AI key" or "add your own Groq or Gemini API key" | Add a key in `.env` (then run `docker compose --profile web up -d`), or in the app under Profile & apps. |
| "Too many requests, slow down" when signing up or signing in | Run `docker compose exec api python -m ease.admin clear-rate-limits`. |
| Tasks pause with a rate-limit message | The free AI tiers allow limited requests per minute and day. Wait a minute and run the task again. |
| A red "Console Error … hydration" box in the browser | A browser extension changed the page. Use a private window, or turn the extension off for localhost. |
| Anything else | `docker compose logs --tail 100 api worker-agent` shows the error. |

<a id="developer-setup"></a>
## Developer setup

Needs **Python 3.12+** and **Node.js 20+** in addition to Docker.

**Frontend with hot reload.** Run the backend in Docker without the `web` profile, then the website with npm:

```bash
docker compose up -d --build
npm --prefix frontend ci
npm --prefix frontend run dev          # http://localhost:3000
```

**Python environment** (for the command-line runner, tests and evaluation):

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e "backend[browser,ml,dev,eval]"   # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m playwright install chromium
```

**Command-line runner (no website).** Runs one task in the terminal and asks for approvals there:

```bash
docker compose up -d postgres redis fixtures
cd backend
../.venv/Scripts/python -m ease.graph.run --prompt "Get the 5 most recent cs.AI papers from arXiv"
# options: --grounding dom|vision|hybrid   --approve ask|yes|no   --no-hitl   --resume-file PATH
```

Put a resume at `private/resume.pdf` (git-ignored) to use matching and form filling from the command line.
`make` shortcuts for the same commands are in the [Makefile](Makefile).

**Optional settings** in `.env`: `BLOCKED_SITES` (extra domains the agent must never open), `REMEMBER_SITE_LOGINS`,
`USER_KEYS_ONLY`, `REGISTRATION_INVITE_CODE`, and `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` for a sign-up human
check. All are described in [.env.example](.env.example).

**Deploying publicly:** step-by-step guides for an AWS EC2 server ([docs/DEPLOY-AWS.md](docs/DEPLOY-AWS.md)) and a
Hugging Face Space ([docs/DEPLOY.md](docs/DEPLOY.md)).

## Tests

From the `backend` folder, with the Python environment above:

```bash
../.venv/Scripts/python -m pytest -q                                  # unit: state machine, security, router, contracts
docker compose up -d postgres redis
../.venv/Scripts/python -m pytest -q -m integration tests/integration  # API against real Postgres + Redis
../.venv/Scripts/python scripts/e2e_hitl_restart.py                    # pause, kill the worker, approve, finish
../.venv/Scripts/python -m ruff check ease tests                       # lint
```

CI (GitHub Actions) runs lint, migrations up/down/up, unit and integration tests, and a gitleaks secret scan on every push.

## Reproducing the evaluation

The benchmark ([backend/ease/eval/benchmark.json](backend/ease/eval/benchmark.json)) has fixture tasks on the local
test sites and live tasks on real websites, each with programmatic checks. Run from `backend` with the fixture sites
up (`docker compose up -d fixtures`) and a resume at `private/resume.pdf` (or pass `--profile-json`). Results are
written to `data/eval/<run-id>.jsonl`.

```bash
python -m ease.eval.harness --suite all --repeats 3                 # fixture and live suites, reported separately
python -m ease.eval.harness --suite all --conditions grounding      # DOM vs vision vs hybrid
python -m ease.eval.harness --suite fixture --conditions hitl       # approvals on vs off
python -m ease.eval.harness --suite all --conditions planner        # hierarchical vs single prompt
python -m ease.eval.report data/eval/<run-id>.jsonl                 # Markdown tables + PNG charts
python -m ease.eval.paper_metrics --fixture <run-id>:<repeats>      # success, time, recovery, intervention, auth, safety
python scripts/workload_scaling.py --levels 1,2,4,8                 # N concurrent users against the running stack
```

(`python` here means the project environment: `../.venv/Scripts/python` on Windows, `../.venv/bin/python` elsewhere.)

Useful harness options: `--cases F01-remote-interns,...` to run a subset, `--pause` between cases for free-tier rate
limits, `--repeat-start` to resume an interrupted run. The harness refuses to start if the fixture sites are down.

**External baselines**, under identical tasks, scoring, model and local sites, each in its own virtual environment:

```bash
<venv with browser-use>/python ease/eval/external_agent.py --cases F01-remote-interns,...
<venv with autogen-ext[magentic-one,openai]>/python ease/eval/external_magentic.py --cases F01-remote-interns,...
```

Every failed run is labelled (`BOT_WALL`, `GROUNDING_MISS`, `PLAN_INVALID`, `PLAN_WRONG`, `AUTH_EXPIRED`,
`TIMEOUT`, `TOOL_ERROR`, `ESCALATED`, `RATE_LIMITED`), and environment failures are reported separately from agent
failures.

## Security

- **Secrets:** AES-256-GCM envelope encryption with a per-record data key and associated data that binds each ciphertext
  to its (user, service) row. Credentials are write-only through the API, decrypted only inside a tool call, and
  redacted from logs by pattern and by value.
- **Auth:** bcrypt; HS256 JWTs with pinned algorithm; single-use rotating refresh tokens; optional invite code and
  Cloudflare Turnstile check for sign-up; users can delete their account and all its data.
- **Abuse / cost:** per-IP and per-email login limits, per-user task and concurrency limits, and atomic LLM budgets
  per task / user / day / deployment, checked before every model call.
- **Agent safety:** SSRF guard on every browser and connector request; navigation pinned to the task's site;
  banking, payment, crypto and government-identity sites blocked; robots.txt respected; no typing into
  password/payment/OTP fields by the model; irreversible actions need approval; page text treated as untrusted data;
  seller trust decided by code, which web content cannot raise.
- **API:** strict CORS, security headers, body-size limit, 404 for other users' resources, single-use WebSocket
  tickets with origin checks, short-lived signed artifact URLs, upload type detection by content.
- **Repo:** `.env` and `private/` are git-ignored; gitleaks runs in pre-commit and CI.

## Scope and ethics

In scope: research, price comparison, browsing and extraction, and form workflows up to (not including)
unapproved submission.
Out of scope: payments, banking, CAPTCHA/2FA bypass, account creation, mass crawling. The agent prepares, the human
submits.

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

## Team

Varun Narayan Singh · Uddesh Pratap Singh · Utkarsh Dubey — guided by Dr. Anshika Agarwal. See
[docs/team-tasks.md](docs/team-tasks.md) and the step-by-step [docs/TEAM-GUIDE.md](docs/TEAM-GUIDE.md).

## Literature

The papers this work builds on (WebVoyager, CowPilot, Agentic Lybic, ColorBrowserAgent, WebUncertainty, Autonoma,
AsyncFlow, the 2025 AI Agent Index) are summarised in [docs/literature.md](docs/literature.md).
