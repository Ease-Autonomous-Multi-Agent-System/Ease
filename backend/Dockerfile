# syntax=docker/dockerfile:1.7
# Layer order matters: heavy, rarely-changing dependencies first; source code last, so a code change rebuilds
# in seconds instead of re-downloading Chromium.

FROM python:3.12-slim AS deps
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_SYMLINKS_WARNING=1 PIP_DEFAULT_TIMEOUT=180 PIP_RETRIES=10
WORKDIR /app
RUN useradd --create-home --uid 10001 ease && mkdir -p /data && chown ease:ease /data
COPY pyproject.toml ./
RUN mkdir ease && touch ease/__init__.py && pip install ".[ml]" flower && rm -rf ease

FROM deps AS agent-deps
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install "playwright>=1.63" && python -m playwright install --with-deps chromium \
    && chmod -R o+rx /ms-playwright

# API / beat / migrations: no browser.
FROM deps AS api
COPY alembic.ini ./
COPY alembic ./alembic
COPY ease ./ease
RUN pip install --no-deps .
USER ease
EXPOSE 8000

# Workers: with Chromium for the browser agent.
FROM agent-deps AS agent
COPY alembic.ini ./
COPY alembic ./alembic
COPY ease ./ease
RUN pip install --no-deps .
USER ease
