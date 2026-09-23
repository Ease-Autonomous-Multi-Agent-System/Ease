# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN useradd --create-home --uid 10001 ease
COPY pyproject.toml ./
# Install deps first (cached layer), then the source.
RUN mkdir ease && touch ease/__init__.py && pip install ".[ml]" flower && rm -rf ease
COPY alembic.ini ./
COPY alembic ./alembic
COPY ease ./ease
RUN pip install --no-deps . && mkdir -p /data && chown ease:ease /data

# API / beat / migrations: no browser.
FROM base AS api
USER ease
EXPOSE 8000

# Worker with Chromium for the browser agent.
FROM base AS agent
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install "playwright>=1.63" && python -m playwright install --with-deps chromium \
    && chmod -R o+rx /ms-playwright
USER ease
# Pre-download the embedding model into the image so the first document upload doesn't stall.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-base-en-v1.5')" || true
