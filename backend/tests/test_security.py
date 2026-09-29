import base64
import uuid

import pytest

from ease.security import auth, netguard, vault
from ease.security.ratelimit import BudgetExceeded, LlmBudget, RateLimiter


# ---------- vault ----------
def test_seal_roundtrip_and_no_plaintext_in_ciphertext():
    kek = b"a" * 32
    aad = b"ease-vault-v1|u1|notion|default"
    secret = "ntn_supersecretvalue1234567890abcdef"
    s = vault.seal(secret, aad, kek)
    assert secret.encode() not in s.ciphertext + s.wrapped_dek
    assert len(s.tag) == 16 and len(s.nonce) == 12
    assert vault.unseal(s, aad, kek) == secret


def test_unseal_rejects_row_swap_between_users():
    kek = b"a" * 32
    s = vault.seal("secret-value", b"ease-vault-v1|alice|notion|default", kek)
    with pytest.raises(vault.VaultError):
        vault.unseal(s, b"ease-vault-v1|mallory|notion|default", kek)


def test_unseal_rejects_tampering_and_wrong_key():
    kek = b"a" * 32
    aad = b"x"
    s = vault.seal("secret-value", aad, kek)
    flipped = bytes([s.ciphertext[0] ^ 1]) + s.ciphertext[1:]
    tampered = vault.Sealed(flipped, s.nonce, s.tag, s.wrapped_dek, s.dek_nonce)
    with pytest.raises(vault.VaultError):
        vault.unseal(tampered, aad, kek)
    with pytest.raises(vault.VaultError):
        vault.unseal(s, aad, b"b" * 32)


def test_rewrap_rotates_master_key():
    old, new = b"a" * 32, b"b" * 32
    s = vault.seal("rotate-me", b"x", old)
    s2 = vault.rewrap(s, b"x", old, new)
    assert s2.ciphertext == s.ciphertext
    assert vault.unseal(s2, b"x", new) == "rotate-me"
    with pytest.raises(vault.VaultError):
        vault.unseal(s2, b"x", old)


def test_mask_hint_never_reveals_secret():
    h = vault.mask_hint("gsk_abcdefghijklmnopqrstuvwxyz123456")
    assert h.endswith("3456") and "abcdefgh" not in h
    assert vault.mask_hint("short") == "…****"


def test_master_key_validation():
    assert len(vault.master_key(base64.b64encode(b"z" * 32).decode())) == 32
    with pytest.raises(vault.VaultError):
        vault.master_key(base64.b64encode(b"z" * 16).decode())


# ---------- auth ----------
def test_password_hash_and_verify():
    h = auth.hash_password("correct horse 42")
    assert auth.verify_password("correct horse 42", h)
    assert not auth.verify_password("wrong horse 42", h)
    assert not auth.verify_password("anything", None)


@pytest.mark.parametrize("pw", ["short1", "onlyletterslong", "12345678901", "x" * 80 + "1"])
def test_weak_passwords_rejected(pw):
    with pytest.raises(auth.AuthError):
        auth.validate_password_strength(pw)


def test_tokens_roundtrip_and_type_confusion():
    uid = uuid.uuid4()
    pair = auth.issue_tokens(uid)
    assert auth.decode_token(pair.access_token, "access")["sub"] == str(uid)
    assert auth.decode_token(pair.refresh_token, "refresh")["jti"] == pair.refresh_jti
    with pytest.raises(auth.AuthError):
        auth.decode_token(pair.refresh_token, "access")  # a refresh token is not an access token


def test_alg_none_token_rejected():
    import jwt as pyjwt

    claims = {"sub": "x", "typ": "access", "iss": "ease", "exp": 9999999999, "iat": 0}
    forged = pyjwt.encode(claims, None, algorithm="none")
    with pytest.raises(auth.AuthError):
        auth.decode_token(forged, "access")


# ---------- rate limits / budgets ----------
def test_rate_limiter_blocks_after_limit(fake_redis):
    rl = RateLimiter(fake_redis)
    assert all(rl.hit("login:1.2.3.4", 3, 60).allowed for _ in range(3))
    res = rl.hit("login:1.2.3.4", 3, 60)
    assert not res.allowed and res.retry_after_s > 0


def test_llm_budget_task_cap(fake_redis, monkeypatch):
    monkeypatch.setenv("TASK_LLM_CALL_BUDGET", "2")
    b = LlmBudget(fake_redis)
    b.consume(user_id="u", task_id="t")
    b.consume(user_id="u", task_id="t")
    with pytest.raises(BudgetExceeded) as ei:
        b.consume(user_id="u", task_id="t")
    assert ei.value.scope == "task"
    # a refused call must not consume from the other scopes
    assert b.usage("u") == {"global_day": 2, "user_day": 2}


def test_llm_budget_global_cap(fake_redis, monkeypatch):
    monkeypatch.setenv("GLOBAL_LLM_CALLS_PER_DAY", "1")
    b = LlmBudget(fake_redis)
    b.consume(user_id="a", task_id="t1")
    with pytest.raises(BudgetExceeded) as ei:
        b.consume(user_id="b", task_id="t2")
    assert ei.value.scope == "global_day"


# ---------- SSRF ----------
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://postgres:5432/",
        "http://metadata.google.internal/",
        "http://user:pw@example.com/",
        "http://example.com:6379/",
    ],
)
def test_ssrf_blocked(url, monkeypatch):
    def fake_resolve(host):
        return ("93.184.215.14",) if host == "example.com" else ("10.0.0.5",)

    monkeypatch.setattr(netguard, "_resolve", fake_resolve)
    with pytest.raises(netguard.BlockedURL):
        netguard.check_url(url)


def test_public_and_fixture_hosts_allowed(monkeypatch):
    monkeypatch.setattr(netguard, "_resolve", lambda h: ("93.184.215.14",))
    assert netguard.check_url("https://export.arxiv.org/api/query")
    assert netguard.check_url("http://fixtures:8080/form.html")  # allowlisted fixture host


def test_dns_to_private_blocked(monkeypatch):
    monkeypatch.setattr(netguard, "_resolve", lambda h: ("127.0.0.1",))
    with pytest.raises(netguard.BlockedURL):
        netguard.check_url("https://evil-rebind.example/")


def test_websocket_origin_rules():
    from ease.api.routes.ws import origin_allowed

    assert origin_allowed("http://localhost:3000", "127.0.0.1:8000")  # allowlisted
    assert origin_allowed("https://demo.trycloudflare.com", "demo.trycloudflare.com")  # same origin via gateway
    assert not origin_allowed("https://evil.example", "demo.trycloudflare.com")
    assert not origin_allowed("javascript://demo.trycloudflare.com", "demo.trycloudflare.com")
