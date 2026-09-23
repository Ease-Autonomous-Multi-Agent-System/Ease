"""FastAPI gateway (layer 2). Validates, persists, enqueues and returns - nothing slow runs here (principle P1)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from ease.api.routes import auth, resources, tasks, ws
from ease.config import get_settings
from ease.db.session import get_engine
from ease.logs import configure_logging, log
from ease.security.ratelimit import get_redis

MAX_BODY = 6 * 1024 * 1024  # a little above the upload limit


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY:
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if request.url.path.startswith(("/auth", "/me", "/credentials")):
            h["Cache-Control"] = "no-store"
        return response


def create_app() -> FastAPI:
    configure_logging()
    s = get_settings()
    prod = s.env == "prod"
    app = FastAPI(title="Ease API", version="0.1.0", docs_url=None if prod else "/docs",
                  redoc_url=None, openapi_url=None if prod else "/openapi.json")
    app.add_middleware(SecurityHeaders)
    app.add_middleware(
        CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
    for r in (auth.router, tasks.router, resources.router, ws.router):
        app.include_router(r)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak stack traces or internals to clients; the log line carries the details (redacted).
        log.exception("api.unhandled", path=request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    def ready() -> JSONResponse:
        checks = {}
        try:
            with get_engine().connect() as c:
                c.execute(text("select 1"))
            checks["postgres"] = "ok"
        except Exception:
            checks["postgres"] = "down"
        try:
            get_redis().ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "down"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(checks, status_code=200 if ok else 503)

    return app


app = create_app()
