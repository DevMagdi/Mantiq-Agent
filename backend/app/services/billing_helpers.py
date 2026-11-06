"""Billing helpers: subscription invoices from the plan and the usage meters.

Data-subject export and erasure used to live here as a second, weaker copy of
``app.services.privacy``. They do not any more. Retention purges call
``privacy.purge_expired``, which redacts through the same function the API uses.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import registry
from app.db.models.entities import Invoice, Plan, Tenant
from app.services import numbers
from app.services.common import meter, usage_summary


async def generate_invoice(session: AsyncSession, tenant: Tenant) -> Invoice:
    plan = (await session.execute(select(Plan).where(Plan.code == tenant.plan_code))).scalar_one_or_none()
    base_price = Decimal(str(plan.price_monthly)) if plan else Decimal("0")
    summary = await usage_summary(session, tenant.id, period_days=30)
    usage = summary["usage"]
    overage = Decimal("0")
    line_items = [{"description": f"{plan.name if plan else tenant.plan_code} subscription (monthly)",
                   "amount": str(base_price)}]
    limits = summary["limits"]
    if usage.get("voice_minute", 0) > limits.get("voice_minutes_per_month", 0):
        extra = usage["voice_minute"] - limits.get("voice_minutes_per_month", 0)
        cost = Decimal(str(round(extra * 0.25, 2)))
        overage += cost
        line_items.append({"description": f"Additional AI voice minutes ({extra:.0f})", "amount": str(cost)})
    if usage.get("rfq", 0) > limits.get("rfqs_per_month", 0):
        extra = usage["rfq"] - limits.get("rfqs_per_month", 0)
        cost = Decimal(str(round(extra * 2, 2)))
        overage += cost
        line_items.append({"description": f"Additional RFQs ({extra:.0f})", "amount": str(cost)})
    amount = numbers.money(base_price + overage, 2)
    # Counting every invoice row into Python just to mint the next number loads the whole ledger.
    count = (await session.execute(
        select(func.count()).select_from(Invoice).where(Invoice.tenant_id == tenant.id)
    )).scalar_one() + 1
    invoice = Invoice(tenant_id=tenant.id, number=f"INV-{datetime.now(UTC).year}-{count:04d}",
                      amount=amount, currency=(plan.currency if plan else "USD"), status="open",
                      issued_at=datetime.now(UTC), lines=line_items)
    session.add(invoice)
    await session.flush()
    gateway = registry.resolve("payment")
    result = await gateway.create_invoice(amount=str(amount), currency=invoice.currency,
                                          reference=invoice.number, description="ProcureAI subscription")
    if result.ok and result.status == "paid":
        invoice.status = "paid"
        invoice.paid_at = datetime.now(UTC)
    await meter(session, tenant_id=tenant.id, kind="invoice", quantity=1, ref_type="invoice", ref_id=invoice.id)
    await session.flush()
    return invoice
