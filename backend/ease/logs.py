"""structlog JSON logging with a redaction processor.

Every log line passes through `redact` before it is rendered, so a secret that accidentally lands in an
event dict (a token in an exception message, an Authorization header) is masked instead of written out.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

from ease.config import get_settings

# Shapes of credentials we know about, masked even if they are not in our own settings.
_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),  # Google API key
    re.compile(r"AQ\.[0-9A-Za-z_\-]{30,}"),  # Google AI Studio key (new format)
    re.compile(r"gsk_[0-9A-Za-z]{30,}"),  # Groq
    re.compile(r"sk-(?:or-|ant-)?[0-9A-Za-z_\-]{20,}"),  # OpenRouter / OpenAI-style keys
    re.compile(r"github_pat_[0-9A-Za-z_]{30,}"),
    re.compile(r"gh[pousr]_[0-9A-Za-z]{30,}"),
    re.compile(r"(?:ntn|secret)_[0-9A-Za-z]{30,}"),  # Notion
    re.compile(r"\d{8,10}:[0-9A-Za-z_\-]{35}"),  # Telegram bot token
    re.compile(r"https://hooks\.slack\.com/services/[0-9A-Za-z/]+"),
    re.compile(r"(?i)bearer\s+[0-9A-Za-z._\-]{16,}"),
    re.compile(r"eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}"),  # JWT
]
_SENSITIVE_KEYS = {"password", "secret", "token", "api_key", "authorization", "cookie", "cookies", "plaintext"}
MASK = "***REDACTED***"


def _known_secrets() -> list[str]:
    return [s for s in get_settings().secret_values() if len(s) >= 8]


def redact_text(text: str, extra: list[str] | None = None) -> str:
    for secret in _known_secrets() + (extra or []):
        if secret:
            text = text.replace(secret, MASK)
    for pat in _SECRET_PATTERNS:
        text = pat.sub(MASK, text)
    return text


def _redact_value(key: str, value: Any) -> Any:
    if key.lower() in _SENSITIVE_KEYS and value:
        return MASK
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value("", v) for v in value]
    return value


def redact(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return {k: _redact_value(k, v) for k, v in event_dict.items()}


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    # httpx logs full request URLs at INFO; some providers put keys in query strings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            redact,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger("ease")
