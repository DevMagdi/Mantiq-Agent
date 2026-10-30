"""Structured logging with PII/secret redaction (NFR-SEC-1)."""
from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

REDACT_KEYS = {
    "password", "password_hash", "hashed_password", "token", "access_token", "refresh_token",
    "secret", "api_key", "authorization", "cookie", "totp_secret", "mfa_secret", "backup_codes",
    "phone", "mobile", "email", "whatsapp_token", "telephony_token", "stripe_key", "llm_api_key",
}
PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+"), r"\1***"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "***@***"),
    (re.compile(r"(\+?\d[\d\s\-]{7,}\d)"), "***PHONE***"),
]


def redact(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: ("***" if k.lower() in REDACT_KEYS else redact(v, k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if isinstance(value, str):
        out = value
        for pattern, repl in PATTERNS:
            out = pattern.sub(repl, out)
        return out
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for attr in ("request_id", "tenant_id", "user_id", "route", "status_code", "latency_ms"):
            if hasattr(record, attr):
                payload[attr] = getattr(record, attr)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").handlers = [handler]
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
