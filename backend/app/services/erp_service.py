"""ERP import of purchase requisitions through the connector abstraction (FR-IN-5)."""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import registry
from app.db.models.entities import Tenant
from app.services import numbers
from app.services.intake import create_pr

log = logging.getLogger("procureai.erp")


async def test_connection() -> dict:
    connector = registry.resolve("erp")
    result = await connector.test_connection()
    return {"connector": getattr(connector, "name", "unknown"), "ok": result.ok,
            "reference": result.reference, "error": result.error}


async def import_requisitions(session: AsyncSession, tenant: Tenant, *, limit: int = 20,
                              requested_by: str | None = None) -> list[dict]:
    connector = registry.resolve("erp")
    rows = await connector.import_requisitions(limit=limit)
    created: list[dict] = []
    for row in rows:
        description = (row.get("description") or row.get("item") or row.get("material") or "").strip()
        if not description:
            continue
        pr = await create_pr(
            session, tenant,
            lines=[{
                "description": description,
                "quantity": str(numbers.parse_decimal(row.get("quantity") or row.get("qty") or 1) or 1),
                "uom": row.get("uom") or row.get("unit") or "pcs",
                "needed_by": row.get("required_date") or "",
                "currency": (row.get("currency") or tenant.currency)[:3],
                "target_price": row.get("price") or row.get("target_price") or "",
            }],
            title=f"ERP import: {description[:80]}",
            source="erp",
            requested_by=requested_by,
            currency=row.get("currency") or tenant.currency,
            raw_input=str(row)[:5000],
        )
        created.append({"pr_id": pr.id, "code": pr.code, "title": pr.title, "source_file": row.get("_source_file", "")})
    return created
