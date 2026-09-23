import base64
import os

import pytest

# Deterministic, obviously-fake secrets for tests. Set before ease.config is imported anywhere.
os.environ.update(
    ENV="test",
    JWT_SECRET="test-jwt-secret-" + "x" * 40,
    VAULT_MASTER_KEY=base64.b64encode(b"k" * 32).decode(),
    REGISTRATION_INVITE_CODE="",
    LLM_CACHE_MODE="off",
    GEMINI_API_KEY="",
    GROQ_API_KEY="",
    OPENROUTER_API_KEY="",
    GITHUB_MODELS_TOKEN="",
    OLLAMA_BASE_URL="",
)


@pytest.fixture(autouse=True)
def _fresh_settings():
    from ease.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_redis():
    import fakeredis

    return fakeredis.FakeRedis(decode_responses=True)
