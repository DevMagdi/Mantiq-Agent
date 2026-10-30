"""Append-only audit trail (FR-AUTH-4, NFR-AI-2)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entities import AuditLog

SENSITIVE_KEYS = {"password", "hashed_password", "totp_secret", "api_key", "token", "secret", "phone", "email"}


def sanitize(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None

    def scrub(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {k: ("***" if k.lower() in SENSITIVE_KEYS else scrub(v, k)) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v, key) for v in value]
        return value

    return scrub(payload)


async def audit(
    session: AsyncSession,
    *,
    tenant_id: str | None,
    action: str,
    actor_type: str = "user",
    actor_id: str | None = None,
    actor_label: str = "",
    entity_type: str = "",
    entity_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    ip: str = "",
    request_id: str = "",
) -> AuditLog:
    entry = AuditLog(
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=sanitize(before),
        after=sanitize(after),
        ip=ip,
        request_id=request_id,
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_audit(
    session: AsyncSession,
    tenant_id: str,
    *,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    filters = [AuditLog.tenant_id == tenant_id]
    if action:
        filters.append(AuditLog.action.like(f"{action}%"))
    if entity_type:
        filters.append(AuditLog.entity_type == entity_type)
    if entity_id:
        filters.append(AuditLog.entity_id == entity_id)
    if actor_type:
        filters.append(AuditLog.actor_type == actor_type)
    where = and_(*filters)
    total = (await session.execute(select(func.count()).select_from(AuditLog).where(where))).scalar_one()
    rows = (
        await session.execute(
            select(AuditLog).where(where).order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return list(rows), int(total)
