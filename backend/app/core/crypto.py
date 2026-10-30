"""Envelope encryption for supplier PII and integration secrets (NFR-SEC-1).

Per-tenant data encryption keys (DEKs) are generated once, wrapped with the master key
(AES-256-GCM) and stored in `tenant.settings['wrapped_dek']`. Ciphertext is stored with
its `key_id` so key rotation can re-wrap without re-encrypting every row.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.entities import Tenant

log = logging.getLogger("procureai.crypto")

_DEK_CACHE: dict[tuple[str, str], bytes] = {}


def _master_key() -> bytes:
    raw = settings.encryption_master_key or settings.resolved_secret()
    if not settings.encryption_master_key and settings.is_prod:
        raise RuntimeError("ENCRYPTION_MASTER_KEY must be set in production.")
    return hashlib.sha256(f"procureai-master:{raw}".encode()).digest()


def _wrap(dek: bytes) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_master_key()).encrypt(nonce, dek, b"dek")
    return base64.b64encode(nonce + ct).decode()


def _unwrap(blob: str) -> bytes:
    data = base64.b64decode(blob)
    return AESGCM(_master_key()).decrypt(data[:12], data[12:], b"dek")


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _b64d(data: str) -> bytes:
    return base64.b64decode(data.encode())


async def get_dek(session: AsyncSession, tenant: Tenant) -> tuple[bytes, str]:
    """Return (dek, key_id), creating and persisting one on first use."""
    stored = tenant.settings or {}
    key_id = stored.get("dek_id", "")
    wrapped = stored.get("wrapped_dek", "")
    if key_id and wrapped:
        cache_key = (tenant.id, key_id)
        if cache_key in _DEK_CACHE:
            return _DEK_CACHE[cache_key], key_id
        dek = _unwrap(wrapped)
        _DEK_CACHE[cache_key] = dek
        return dek, key_id
    dek = AESGCM.generate_key(bit_length=256)
    key_id = f"k{os.urandom(4).hex()}"
    settings_json = dict(stored)
    settings_json.update({"dek_id": key_id, "wrapped_dek": _wrap(dek)})
    tenant.settings = settings_json
    session.add(tenant)
    _DEK_CACHE[(tenant.id, key_id)] = dek
    return dek, key_id


async def encrypt_text(session: AsyncSession, tenant: Tenant, plaintext: str) -> tuple[str, str]:
    if not plaintext:
        return "", ""
    dek, key_id = await get_dek(session, tenant)
    nonce = os.urandom(12)
    ct = AESGCM(dek).encrypt(nonce, plaintext.encode(), tenant.id.encode())
    return _b64e(nonce + ct), key_id


async def decrypt_text(session: AsyncSession, tenant: Tenant, ciphertext: str, key_id: str) -> str:
    if not ciphertext:
        return ""
    dek, current_id = await get_dek(session, tenant)
    if key_id and key_id != current_id:
        log.warning("ciphertext encrypted with retired key %s (current %s)", key_id, current_id)
    data = _b64d(ciphertext)
    try:
        return AESGCM(dek).decrypt(data[:12], data[12:], tenant.id.encode()).decode()
    except Exception:  # pragma: no cover - corrupt/rotated ciphertext
        return ""


def encrypt_secret_raw(plaintext: str) -> str:
    """Encrypt a value that is not tenant-scoped (e.g. a wrapped backup)."""
    nonce = os.urandom(12)
    return _b64e(nonce + AESGCM(_master_key()).encrypt(nonce, plaintext.encode(), b"raw"))


def decrypt_secret_raw(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    data = _b64d(ciphertext)
    return AESGCM(_master_key()).decrypt(data[:12], data[12:], b"raw").decode()


def redact_pii(value: str) -> str:
    """Mask emails/phones before logging or exporting."""
    import re

    if not value:
        return ""
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:2]}***@{domain}"
    digits = re.sub(r"\D", "", value)
    return f"***{digits[-3:]}" if digits else "***"
