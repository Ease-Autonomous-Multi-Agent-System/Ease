# Ease in ONE container, for a free Hugging Face Docker Space (2 vCPU, 16 GB RAM, public HTTPS URL).
#
# Everything the docker-compose stack runs as separate services runs here under supervisord: PostgreSQL 16 +
# pgvector, Redis, the FastAPI API, a Celery worker with Chromium, the Next.js web app, the demo test sites and an
# nginx gateway on port 7860 (the only port Hugging Face exposes). Data lives in the container and resets when the
# Space restarts - fine for a demo server.
#
# The source is cloned from GitHub, so the Space only needs this file and README.md. Local test build, using the
# working tree instead of GitHub:
#   docker build -f deploy/hf/Dockerfile --build-context src=. -t ease-hf .
#   docker run --rm -p 127.0.0.1:7860:7860 -e REGISTRATION_INVITE_CODE=... -e GROQ_API_KEY=... ease-hf

# ---------- source ----------
FROM alpine/git:2.47.2 AS clone
ARG REPO=https://github.com/Ease-Autonomous-Multi-Agent-System/Ease.git
ARG REF=main
RUN git clone --depth 1 --branch "$REF" "$REPO" /repo && rm -rf /repo/.git

FROM scratch AS src
COPY --from=clone /repo /

# ---------- web app ----------
FROM node:22-bookworm-slim AS web
WORKDIR /app
COPY --from=src /frontend/package.json /frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY --from=src /frontend/next.config.ts /frontend/tsconfig.json /frontend/next-env.d.ts ./
RUN mkdir -p public  # git keeps no empty folders; the app has no static files of its own yet
COPY --from=src /frontend/src ./src
ENV NEXT_PUBLIC_API_URL=/backend NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---------- everything else ----------
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=180 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright DEBIAN_FRONTEND=noninteractive

# PostgreSQL 16 + pgvector from the official PostgreSQL apt repository; Redis, nginx and supervisord from Debian.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
         https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
         postgresql-16 postgresql-16-pgvector redis-server nginx supervisor \
    && rm -rf /var/lib/apt/lists/*

COPY --from=web /usr/local/bin/node /usr/local/bin/node

WORKDIR /app/backend
COPY --from=src /backend/pyproject.toml ./
RUN mkdir ease && touch ease/__init__.py && pip install ".[ml,browser]" && rm -rf ease \
    && python -m playwright install --with-deps chromium && chmod -R o+rx /ms-playwright

COPY --from=src /backend/alembic.ini ./
COPY --from=src /backend/alembic ./alembic
COPY --from=src /backend/ease ./ease
RUN pip install --no-deps .

COPY --from=web /app/.next/standalone /app/web
COPY --from=web /app/.next/static /app/web/.next/static
COPY --from=web /app/public /app/web/public
COPY --from=src /fixtures /app/fixtures
COPY --from=src /deploy/hf/start.sh /deploy/hf/supervisord.conf /deploy/hf/nginx.conf /app/deploy/

# Hugging Face runs the container as uid 1000; Postgres refuses to run as root anyway.
RUN useradd -m -u 1000 user && chmod +x /app/deploy/start.sh
USER user
# USER_KEYS_ONLY: a public website where every visitor uses their own AI and service keys (see docs/DEPLOY.md).
ENV HOME=/home/user DATA_DIR=/home/user/data ENV=prod USER_KEYS_ONLY=true \
    REDIS_URL=redis://127.0.0.1:6379 \
    ARTIFACTS_DIR=/home/user/data/artifacts UPLOADS_DIR=/home/user/data/uploads \
    LLM_CACHE_PATH=/home/user/data/llm_cache.sqlite3 \
    FIXTURES_BASE_URL=http://127.0.0.1:8080 FIXTURE_HOSTS=127.0.0.1 FIXTURE_PORTS=8080 \
    USER_LLM_CALLS_PER_DAY=150 USER_TASKS_PER_HOUR=15 HF_HUB_DISABLE_TELEMETRY=1
EXPOSE 7860
CMD ["/app/deploy/start.sh"]
