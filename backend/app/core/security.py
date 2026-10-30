"""Password hashing, JWT sessions, TOTP MFA and password policy."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789", "qwerty123",
    "letmein", "admin123", "welcome1", "changeme", "iloveyou", "procureai",
}


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return _hasher.verify(hashed, password)
    except VerifyMismatchError:
        return False
    except Exception:  # malformed hash
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except Exception:
        return False


def password_policy_errors(password: str, email: str = "") -> list[str]:
    """Enterprise password policy: 12+ chars, mixed classes, no email local-part, not common."""
    errors: list[str] = []
    if len(password) < 12:
        errors.append("Password must be at least 12 characters.")
    if not any(c.islower() for c in password):
        errors.append("Password must contain a lowercase letter.")
    if not any(c.isupper() for c in password):
        errors.append("Password must contain an uppercase letter.")
    if not any(c.isdigit() for c in password):
        errors.append("Password must contain a digit.")
    if not any(not c.isalnum() for c in password):
        errors.append("Password must contain a symbol.")
    if email:
        local = email.split("@")[0].lower()
        if local and len(local) >= 3 and local in password.lower():
            errors.append("Password must not contain the email local-part.")
    if password.lower() in COMMON_PASSWORDS:
        errors.append("Password is too common.")
    return errors


# ---------------------------------------------------------------- tokens
def _encode(payload: dict[str, Any], ttl: timedelta, token_type: str) -> str:
    now = datetime.now(UTC)
    # A caller that supplies a jti is binding the token to a stored session row, so it must
    # survive; otherwise a fresh one is minted. (Overwriting it here would make every refresh
    # token unmatchable and look like a revoked session.)
    body = {
        "jti": secrets.token_urlsafe(12),
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "type": token_type,
    }
    return jwt.encode(body, settings.resolved_secret(), algorithm="HS256")


def create_access_token(*, user_id: str, tenant_id: str, roles: list[str], is_superadmin: bool = False) -> str:
    return _encode(
        {"sub": user_id, "tenant_id": tenant_id, "roles": roles, "sa": is_superadmin},
        timedelta(minutes=settings.access_token_ttl_minutes),
        "access",
    )


def create_refresh_token(*, user_id: str, tenant_id: str, jti: str | None = None) -> str:
    return _encode({"sub": user_id, "tenant_id": tenant_id, "jti": jti or secrets.token_urlsafe(24)},
                   timedelta(days=settings.refresh_token_ttl_days), "refresh")


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.resolved_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:  # expired / tampered
        raise ValueError("invalid_token") from exc
    if payload.get("type") != expected_type:
        raise ValueError("invalid_token_type")
    return payload


def random_password(length: int = 20) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if not password_policy_errors(pwd):
            return pwd


# ---------------------------------------------------------------- TOTP (RFC 6238)
def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def _totp_at(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_now(secret: str, at: float | None = None) -> str:
    return _totp_at(secret, int((at or time.time()) // 30))


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    if not secret or not code:
        return False
    counter = int(time.time() // 30)
    for drift in range(-window, window + 1):
        if hmac.compare_digest(_totp_at(secret, counter + drift), code.strip()):
            return True
    return False


def provisioning_uri(secret: str, email: str, issuer: str = "ProcureAI") -> str:
    from urllib.parse import quote

    label = quote(f"{issuer}:{email}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def generate_backup_codes(n: int = 8) -> list[str]:
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]


def hash_backup_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


# ---------------------------------------------------------------- misc
def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a or "", b or "")


def sign_payload(payload: str, purpose: str = "generic") -> str:
    """HMAC signature used for web-form links and webhooks."""
    return hmac.new(settings.resolved_secret().encode(), f"{purpose}:{payload}".encode(), hashlib.sha256).hexdigest()


def verify_signature(payload: str, signature: str, purpose: str = "generic") -> bool:
    return constant_time_equals(sign_payload(payload, purpose), signature)


def mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    return "*" * max(0, len(value) - keep) + value[-keep:]
