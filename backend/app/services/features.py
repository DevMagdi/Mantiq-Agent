"""Tenant feature flags for the phase-3 channel and AI-voice features.

Two layers must agree before a gated capability is used:

1. the **deployment** switch in `settings` (`WHATSAPP_ENABLED`, `VOICE_AI_ENABLED`,
   `VOICE_ASSISTANT_ENABLED`) — an operator can turn a capability off for the whole installation;
2. the **tenant** flag in `tenant.settings["features"]` — an administrator turns it on per tenant.

Resolving both in one place is what makes "feature flags turning voice features on and off cleanly"
testable: every entry point asks `is_enabled()` and no code path reads the raw JSON. Unknown keys are
rejected rather than stored, so a typo cannot silently disable a control.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.entities import Tenant

#: Feature key -> (global setting attribute, default per tenant, label, Arabic label).
FEATURES: dict[str, tuple[str, bool, str, str]] = {
    "whatsapp": (
        "whatsapp_enabled", True,
        "WhatsApp as a supplier channel", "واتساب كقناة تواصل مع الموردين",
    ),
    "whatsapp_rich_messages": (
        "whatsapp_enabled", True,
        "WhatsApp documents and structured quote requests", "مرفقات واتساب وطلب عرض سعر منظم",
    ),
    "voice_ai_calls": (
        "voice_ai_enabled", True,
        "Outbound AI voice calls to suppliers (disclosure + consent)", "مكالمات آلية صادرة للموردين",
    ),
    "voice_assistant": (
        "voice_assistant_enabled", True,
        "Internal voice assistant (read-only, RBAC-scoped)", "المساعد الصوتي الداخلي",
    ),
    "voice_recording": (
        "voice_ai_enabled", True,
        "Call recording when the supplier consents", "تسجيل المكالمات بموافقة المورد",
    ),
    "channel_switching": (
        "whatsapp_enabled", True,
        "Switch a conversation to another channel mid-thread", "تحويل المحادثة لقناة أخرى",
    ),
}

DEFAULT_FLAGS: dict[str, bool] = {key: value[1] for key, value in FEATURES.items()}


def global_allows(key: str) -> bool:
    spec = FEATURES.get(key)
    if spec is None:
        return False
    return bool(getattr(settings, spec[0], False))


def tenant_flags(tenant: Tenant | None) -> dict[str, bool]:
    """Stored tenant overrides merged over the defaults (never over the global switch)."""
    flags = dict(DEFAULT_FLAGS)
    if tenant is not None:
        stored = (tenant.settings or {}).get("features") or {}
        for key, value in stored.items():
            if key in FEATURES and isinstance(value, bool):
                flags[key] = value
    return flags


def is_enabled(tenant: Tenant | None, key: str) -> bool:
    if key not in FEATURES:
        raise KeyError(f"Unknown feature flag: {key}")
    return global_allows(key) and tenant_flags(tenant).get(key, False)


def describe(tenant: Tenant | None) -> list[dict[str, Any]]:
    """UI payload: the effective state plus *why* it is off, so the screen can explain itself."""
    flags = tenant_flags(tenant)
    rows: list[dict[str, Any]] = []
    for key, spec in FEATURES.items():
        allowed_globally = global_allows(key)
        rows.append({
            "key": key,
            "enabled": allowed_globally and flags[key],
            "tenant_value": flags[key],
            "global_allowed": allowed_globally,
            "label": spec[2],
            "label_ar": spec[3],
            "reason": "" if allowed_globally and flags[key]
                      else ("disabled_by_operator" if not allowed_globally else "disabled_by_tenant"),
        })
    return rows


def apply(tenant: Tenant, patch: dict[str, Any]) -> dict[str, bool]:
    """Validate and merge a patch. Raises `ValueError` for an unknown key or a non-boolean value."""
    unknown = sorted(set(patch) - set(FEATURES))
    if unknown:
        raise ValueError(f"Unknown feature flag(s): {', '.join(unknown)}")
    for key, value in patch.items():
        if not isinstance(value, bool):
            raise ValueError(f"Feature flag '{key}' must be true or false.")
    current = dict((tenant.settings or {}).get("features") or {})
    current.update(patch)
    tenant.settings = {**(tenant.settings or {}), "features": current}
    return tenant_flags(tenant)


async def update(session: AsyncSession, tenant: Tenant, patch: dict[str, Any]) -> dict[str, Any]:
    flags = apply(tenant, patch)
    await session.flush()
    return {"flags": flags, "features": describe(tenant)}
