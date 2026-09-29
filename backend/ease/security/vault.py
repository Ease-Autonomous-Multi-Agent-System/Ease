"""AES-256-GCM envelope-encrypted credential vault (components C8 / C24).

Design:
 * Each secret is encrypted with its own random 256-bit data key (DEK) under AES-GCM.
 * The DEK is itself encrypted ("wrapped") with the master key (KEK) from VAULT_MASTER_KEY.
 * Associated data binds each ciphertext to its (user, service, name) row, so copying a ciphertext into
   another user's row makes decryption fail instead of leaking a credential across accounts.
 * Key rotation only re-wraps DEKs (see scripts/rotate_vault_key.py); secret ciphertexts never change.
 * Plaintext only exists inside `Vault.get()`'s caller - the tool boundary. It is never returned by the
   API, put in GraphState, written to a checkpoint, logged, or sent to an LLM.
"""

from __future__ import annotations

import base64
import os
import uuid
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from ease.config import get_settings
from ease.db.models import AuditLog, Credential, CredentialKind

NONCE_BYTES = 12
TAG_BYTES = 16


class VaultError(Exception):
    pass


@dataclass(frozen=True)
class Sealed:
    ciphertext: bytes
    nonce: bytes
    tag: bytes
    wrapped_dek: bytes
    dek_nonce: bytes


def _aad(user_id: uuid.UUID | str, service: str, name: str) -> bytes:
    return f"ease-vault-v1|{user_id}|{service}|{name}".encode()


def master_key(raw_b64: str | None = None) -> bytes:
    raw = raw_b64 if raw_b64 is not None else get_settings().vault_master_key.get_secret_value()
    if not raw:
        raise VaultError("VAULT_MASTER_KEY is not configured")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise VaultError("VAULT_MASTER_KEY must decode to 32 bytes")
    return key


def seal(plaintext: str, aad: bytes, kek: bytes) -> Sealed:
    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(NONCE_BYTES)
    ct_and_tag = AESGCM(dek).encrypt(nonce, plaintext.encode(), aad)
    dek_nonce = os.urandom(NONCE_BYTES)
    wrapped = AESGCM(kek).encrypt(dek_nonce, dek, aad)
    return Sealed(ct_and_tag[:-TAG_BYTES], nonce, ct_and_tag[-TAG_BYTES:], wrapped, dek_nonce)


def unseal(s: Sealed, aad: bytes, kek: bytes) -> str:
    try:
        dek = AESGCM(kek).decrypt(s.dek_nonce, s.wrapped_dek, aad)
        return AESGCM(dek).decrypt(s.nonce, s.ciphertext + s.tag, aad).decode()
    except Exception as exc:  # InvalidTag and friends - never include key material in the message
        raise VaultError("credential could not be decrypted (wrong key or tampered row)") from exc


def rewrap(s: Sealed, aad: bytes, old_kek: bytes, new_kek: bytes) -> Sealed:
    try:
        dek = AESGCM(old_kek).decrypt(s.dek_nonce, s.wrapped_dek, aad)
    except Exception as exc:
        raise VaultError("cannot unwrap DEK with the old key") from exc
    dek_nonce = os.urandom(NONCE_BYTES)
    return Sealed(s.ciphertext, s.nonce, s.tag, AESGCM(new_kek).encrypt(dek_nonce, dek, aad), dek_nonce)


def mask_hint(secret: str) -> str:
    """What the UI may show: a prefix and the last 4 characters, never enough to reconstruct the value."""
    if len(secret) <= 8:
        return "…" + "*" * 4
    prefix = secret[:4] if not secret[:4].isalnum() or "_" in secret[:5] else secret[:2]
    return f"{prefix}…{secret[-4:]}"


def parse_ref(ref: str) -> tuple[str, str]:
    service, _, name = ref.partition(":")
    if not service or not name:
        raise VaultError(f"bad credential ref {ref!r}")
    return service, name


class Vault:
    def __init__(self, session: Session, kek: bytes | None = None):
        self.session = session
        self._kek = kek

    @property
    def kek(self) -> bytes:
        if self._kek is None:
            self._kek = master_key()
        return self._kek

    def put(
        self, user_id: uuid.UUID, service: str, name: str, secret: str, kind: CredentialKind = CredentialKind.API_KEY,
        hint: str | None = None,
    ) -> Credential:
        sealed = seal(secret, _aad(user_id, service, name), self.kek)
        row = self.session.scalar(
            select(Credential).where(
                Credential.user_id == user_id, Credential.service == service, Credential.name == name
            )
        )
        if row is None:
            row = Credential(user_id=user_id, service=service, name=name, kind=kind)
            self.session.add(row)
        row.kind = kind
        row.ciphertext, row.nonce, row.tag = sealed.ciphertext, sealed.nonce, sealed.tag
        row.wrapped_dek, row.dek_nonce = sealed.wrapped_dek, sealed.dek_nonce
        row.hint = hint if hint is not None else mask_hint(secret)  # logins show the username, never the password
        self.session.add(AuditLog(user_id=user_id, action="vault.put", resource=f"{service}:{name}"))
        self.session.flush()
        return row

    def get(self, user_id: uuid.UUID | str, ref: str, *, task_id: uuid.UUID | str | None = None) -> str | None:
        """Decrypt a credential. Call only inside a tool's execution scope and hand the value straight to the
        HTTP client / browser context."""
        service, name = parse_ref(ref)
        uid = uuid.UUID(str(user_id))
        row = self.session.scalar(
            select(Credential).where(Credential.user_id == uid, Credential.service == service, Credential.name == name)
        )
        if row is None:
            return None
        self.session.add(
            AuditLog(
                user_id=uid,
                task_id=uuid.UUID(str(task_id)) if task_id else None,
                action="vault.get",
                resource=ref,
            )
        )
        return unseal(
            Sealed(row.ciphertext, row.nonce, row.tag, row.wrapped_dek, row.dek_nonce),
            _aad(uid, service, name),
            self.kek,
        )

    def delete(self, user_id: uuid.UUID, ref: str) -> bool:
        service, name = parse_ref(ref)
        row = self.session.scalar(
            select(Credential).where(
                Credential.user_id == user_id, Credential.service == service, Credential.name == name
            )
        )
        if row is None:
            return False
        self.session.delete(row)
        self.session.add(AuditLog(user_id=user_id, action="vault.delete", resource=ref))
        return True
