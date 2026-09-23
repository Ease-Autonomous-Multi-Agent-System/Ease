"""Runtime configuration, loaded from environment variables / the repo-root .env file."""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    env: str = "dev"  # dev | test | prod

    # --- infrastructure ---
    database_url: str = "postgresql+psycopg://ease:ease@localhost:5432/ease"
    redis_url: str = "redis://localhost:6379"

    # --- auth ---
    jwt_secret: SecretStr = SecretStr("")
    jwt_access_ttl_s: int = 15 * 60
    jwt_refresh_ttl_s: int = 7 * 24 * 3600
    # When set, /auth/register requires this code. Stops strangers from signing up and burning LLM quota.
    registration_invite_code: SecretStr | None = None

    # --- vault ---
    vault_master_key: SecretStr = SecretStr("")

    # --- LLM providers (all optional; router skips providers without a key) ---
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    github_models_token: SecretStr | None = None
    ollama_base_url: str | None = None
    llm_provider_order: str = "gemini,openrouter,github,ollama"  # used when an image is attached
    llm_text_provider_order: str = "groq,gemini,openrouter,github,ollama"  # text-only calls: fastest first
    llm_cache_mode: str = "readwrite"  # off | readwrite | replay
    llm_cache_path: Path = REPO_ROOT / "data" / "llm_cache.sqlite3"

    # --- abuse / cost limits (the "don't let anyone exhaust the free tier" knobs) ---
    max_prompt_chars: int = 2000
    max_upload_bytes: int = 5 * 1024 * 1024
    user_tasks_per_hour: int = 20
    user_concurrent_tasks: int = 2
    user_llm_calls_per_day: int = 400
    global_llm_calls_per_day: int = 1500
    task_llm_call_budget: int = 80
    max_plan_steps: int = 8
    max_browser_actions_per_step: int = 20
    login_attempts_per_minute: int = 5

    # --- connectors ---
    notion_token: SecretStr | None = None
    notion_database_id: str | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    slack_webhook_url: SecretStr | None = None
    google_service_account_file: Path | None = None
    google_sheet_id: str | None = None

    # --- browser / network safety ---
    # Hosts the browser + API agents may reach even though they resolve to private IPs (local fixture sites).
    fixture_hosts: str = "fixtures,localhost,127.0.0.1"
    browser_headless: bool = True
    browser_cdp_url: str | None = None  # connect to a real Chrome (host mode) instead of launching one
    respect_robots_txt: bool = True
    # When running outside Docker, "http://fixtures:8080" is rewritten to this (e.g. http://127.0.0.1:8080).
    fixtures_base_url: str | None = None

    # --- paths ---
    artifacts_dir: Path = REPO_ROOT / "data" / "artifacts"
    uploads_dir: Path = REPO_ROOT / "data" / "uploads"

    # --- web ---
    cors_origins: str = "http://localhost:3000"

    @field_validator("vault_master_key")
    @classmethod
    def _check_vault_key(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if raw and len(base64.b64decode(raw)) != 32:
            raise ValueError("VAULT_MASTER_KEY must be base64 of exactly 32 bytes (AES-256)")
        return v

    @property
    def fixture_host_set(self) -> set[str]:
        return {h.strip().lower() for h in self.fixture_hosts.split(",") if h.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def secret_values(self) -> list[str]:
        """Every configured secret, for the log redactor and the no-plaintext tests."""
        out = []
        for name in type(self).model_fields:
            val = getattr(self, name)
            if isinstance(val, SecretStr) and val.get_secret_value():
                out.append(val.get_secret_value())
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()

