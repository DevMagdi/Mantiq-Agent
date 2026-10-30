"""Realistic Egyptian-market demo data for one tenant (Section 14). Never runs in prod."""
from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models.entities import (
    Approval,
    ApprovalChain,
    BenchmarkPrice,
    Comment,
    GoodsReceipt,
    Offer,
    OfferLine,
    Plant,
    PriceHistory,
    RFQLine,
    RFQSupplier,
    Supplier,
    SupplierCertification,
    SupplierInvoice,
    Tenant,
    User,
)
from app.services import invoicing, receiving
from app.services.catalog_data import CATEGORIES, DEMO_PLANTS, DEMO_SUPPLIERS
from app.services.intake import create_pr
from app.services.negotiation import upsert_policy
from app.services.po import create_po_for_offer
from app.services.rfq import create_rfq_from_pr, dispatch_rfq
from app.services.suppliers import create_supplier

log = logging.getLogger("procureai.seed")
random.seed(11)

EXTRA_SUPPLIER_NAMES = [
    ("El-Mahmoudia Metals", "المحمودية للمعادن", "EG", "Cairo", "STEEL"),
    ("Helwan Steel Works", "حديد حلوان", "EG", "Helwan", "STEEL"),
    ("Sinai Packaging", "سيناء للتغليف", "EG", "Ismailia", "PACKAGING"),
    ("Modern Carton Co.", "الحديثة للكرتون", "EG", "10th of Ramadan", "PACKAGING"),
    ("Misr Lubricants", "مصر للزيوت", "EG", "Cairo", "LUBRICANTS"),
    ("Gulf Oil Egypt", "جلف أويل مصر", "EG", "Alexandria", "LUBRICANTS"),
    ("Cairo Bearing House", "دار القاهرة للرولمان", "EG", "Cairo", "SPARE_PARTS"),
    ("Delta Pumps", "دلتا للمضخات", "EG", "Tanta", "SPARE_PARTS"),
    ("Nile Electrical", "النيل للكهرباء", "EG", "Giza", "ELECTRICAL"),
    ("Emirates Cable Co.", "الإمارات للكابلات", "AE", "Dubai", "ELECTRICAL"),
    ("Jeddah Industrial", "جدة الصناعية", "SA", "Jeddah", "MRO"),
    ("Kuwait Supplies House", "بيت التوريدات الكويتي", "KW", "Kuwait City", "MRO"),
    ("Ramadan Industrial Co.", "رمضان للتوريدات الصناعية", "EG", "10th of Ramadan", "MRO"),
    ("El-Sewedy Cables", "السويدي للكابلات", "EG", "Cairo", "ELECTRICAL"),
    ("Alexandria Bearings", "الإسكندرية للرولمان", "EG", "Alexandria", "SPARE_PARTS"),
    ("Port Said Trading", "بورسعيد للتجارة", "EG", "Port Said", "MRO"),
    ("Giza Plastics", "الجيزة للبلاستيك", "EG", "Giza", "PACKAGING"),
    ("Suez Steel Traders", "تجار حديد السويس", "EG", "Suez", "STEEL"),
    ("Abu Dhabi Industrial", "أبوظبي الصناعية", "AE", "Abu Dhabi", "MRO"),
    ("Riyadh Metals", "الرياض للمعادن", "SA", "Riyadh", "STEEL"),
    ("Nile Cement Traders", "تجار أسمنت النيل", "EG", "Cairo", "MRO"),
    ("Obour Industrial Supplies", "العبور للتوريدات", "EG", "Obour", "MRO"),
    ("Egypt Safety Equipment", "مصر لمعدات السلامة", "EG", "Cairo", "MRO"),
    ("Damanhour Packaging", "دمنهور للتغليف", "EG", "Damanhour", "PACKAGING"),
    ("Tanta Metals", "طنطا للمعادن", "EG", "Tanta", "STEEL"),
    ("Saudi Lubricants", "الزيوت السعودية", "SA", "Dammam", "LUBRICANTS"),
    ("Gulf Bearings", "الخليج للرولمان", "AE", "Sharjah", "SPARE_PARTS"),
]


def _make_price_history(session: AsyncSession, tenant: Tenant, item_key: str, base_price: float,
                        supplier_id: str | None, months: int = 8) -> None:
    for month in range(months, 0, -1):
        drift = 1 + random.uniform(-0.035, 0.045)
        price = round(base_price * (1 + (months - month) * 0.004) * drift, 2)
        session.add(PriceHistory(
            tenant_id=tenant.id, item_key=item_key, supplier_id=supplier_id,
            unit_price=Decimal(str(price)), currency="EGP", unit_price_base=Decimal(str(price)),
            observed_at=datetime.now(UTC) - timedelta(days=month * 30),
        ))
    for period in ["last_month", "last_quarter"]:
        session.add(BenchmarkPrice(
            tenant_id=tenant.id, item_key=item_key, period=period,
            p25=Decimal(str(round(base_price * 0.94, 2))), p50=Decimal(str(round(base_price, 2))),
            p75=Decimal(str(round(base_price * 1.07, 2))), currency="EGP", sample_size=12,
        ))



async def _seed_delivery(session: AsyncSession, tenant: Tenant, po, users: dict[str, User]) -> None:
    """Walk the demo PO through one short delivery, an invoice and a partial payment.

    The story a demo user sees: half the steel arrived first, was confirmed by the requester and
    invoiced; a partial payment settled 40% of that invoice; the balance then arrived and the PO
    is waiting to be closed.
    """
    lines = receiving.po_lines(po)
    await receiving.mark_in_transit(session, tenant, po, actor_id=users["buyer"].id,
                                    carrier="Nile Logistics", expected_at=datetime.now(UTC) + timedelta(days=3))
    first = [{"line_no": row["line_no"],
              "received_qty": str(row["quantity"] * Decimal("0.6") if index == 0 else row["quantity"]),
              "accepted_qty": str(row["quantity"] * Decimal("0.6") if index == 0 else row["quantity"]),
              "condition": "ok"}
             for index, row in enumerate(lines)]
    receipt = await receiving.create_receipt(
        session, tenant, po, lines=first, received_by=users["buyer"].id,
        delivery_note_ref="DN-88421", carrier="Nile Logistics", warehouse="Alexandria central stores",
        notes="First delivery; steel arrived in two trucks.",
        received_at=datetime.now(UTC) - timedelta(days=4),
    )
    receipt_row = await session.get(GoodsReceipt, receipt["receipt_id"])
    await receiving.confirm_receipt(session, tenant, receipt_row, confirmed_by=users["requester"].id,
                                    checks={"quantity_ok": True, "quality_ok": True, "spec_ok": True},
                                    comment="Rebar and film match the specification.",
                                    decision="confirm")
    invoice_line_rows = []
    for index, row in enumerate(lines):
        quantity = Decimal(row["quantity"]) * Decimal("0.6") if index == 0 else Decimal(row["quantity"])
        invoice_line_rows.append({"line_no": row["line_no"], "description": row["description"],
                                  "uom": row["uom"], "quantity": str(quantity),
                                  "unit_price": str(row["unit_price"])})
    invoice = await invoicing.create_invoice(
        session, tenant, po, number=f"INV-{po.code.split('-')[-1]}", lines=invoice_line_rows,
        received_by=users["finance_approver"].id, currency=po.currency, tax_amount="0",
        invoice_date=datetime.now(UTC) - timedelta(days=3), due_date=datetime.now(UTC) + timedelta(days=27),
        notes="Delivered quantity only; balance to be invoiced on the second delivery.",
    )
    invoice_row = await session.get(SupplierInvoice, invoice["invoice_id"])
    await invoicing.approve_invoice(session, tenant, invoice_row, approved_by=users["finance_approver"].id,
                                    comment="Matched against the confirmed receipt.")
    await invoicing.record_payment(session, tenant, invoice_row, amount=str(invoice_row.total * Decimal("0.4")),
                                   method="bank_transfer", reference="CIB-TRF-90211",
                                   actor_id=users["finance_approver"].id, note="First instalment")
    remaining = [{"line_no": row["line_no"], "received_qty": str(Decimal(row["quantity"]) * Decimal("0.4")),
                  "accepted_qty": str(Decimal(row["quantity"]) * Decimal("0.4"))}
                 for index, row in enumerate(lines) if index == 0]
    second = await receiving.create_receipt(session, tenant, po, lines=remaining,
                                            received_by=users["buyer"].id,
                                            delivery_note_ref="DN-88510", carrier="Nile Logistics",
                                            warehouse="Alexandria central stores",
                                            received_at=datetime.now(UTC) - timedelta(days=1))
    second_row = await session.get(GoodsReceipt, second["receipt_id"])
    await receiving.confirm_receipt(session, tenant, second_row, confirmed_by=users["requester"].id,
                                    checks={"quantity_ok": True, "quality_ok": True, "spec_ok": True},
                                    comment="Balance of the steel received; weights verified.",
                                    decision="confirm")
    await session.flush()

async def seed_demo_tenant(
    session: AsyncSession, slug: str = "nile", *, demo_password: str = "Demo!Pilot2026"
) -> dict:
    """Seed reference data and the demo dataset for one tenant (used by the CLI and startup)."""
    from app.services.bootstrap import ensure_tenant, seed_reference_data

    await seed_reference_data(session)
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        tenant = await ensure_tenant(session, name="Nile Manufacturing Group", slug=slug)
    return await seed_demo(session, tenant, demo_password=demo_password)


async def seed_demo(session: AsyncSession, tenant: Tenant, *, demo_password: str = "Demo!Pilot2026") -> dict:
    if (tenant.settings or {}).get("demo_seeded"):
        return {"skipped": True, "reason": "demo data already seeded"}
    if func is None:  # pragma: no cover
        raise RuntimeError

    plants = []
    for plant_def in DEMO_PLANTS:
        plant = Plant(tenant_id=tenant.id, **plant_def)
        session.add(plant)
        plants.append(plant)
    await session.flush()

    users = {}
    for email, name, name_ar, role in [
        ("manager@nile.example.com", "Hala Mansour", "هالة منصور", "procurement_manager"),
        ("buyer@nile.example.com", "Omar Khaled", "عمر خالد", "buyer"),
        ("requester@nile.example.com", "Mariam Adel", "مريم عادل", "requester"),
        ("finance@nile.example.com", "Tarek Nabil", "طارق نبيل", "finance_approver"),
        ("storekeeper@nile.example.com", "Youssef Samir", "يوسف سمير", "warehouse"),
    ]:
        user = (await session.execute(
            select(User).where(User.tenant_id == tenant.id, User.email == email)
        )).scalar_one_or_none()
        if not user:
            user = User(tenant_id=tenant.id, email=email, hashed_password=hash_password(demo_password),
                        full_name=name, full_name_ar=name_ar, roles=[role], primary_role=role,
                        must_change_password=False, job_title=role.replace("_", " ").title(),
                        locale="ar")
            session.add(user)
        users[role] = user
    await session.flush()

    supplier_defs = list(DEMO_SUPPLIERS) + [
        {"name": name, "name_ar": name_ar, "country": country, "city": city, "category": category,
         "score": random.randint(65, 92), "website": f"https://{name.lower().replace(' ', '-').replace('.', '')}.example.com",
         "email": f"sales@{name.lower().replace(' ', '')[:18]}.example.com",
         "phone": f"+20 1{random.randint(0, 2)} {random.randint(100, 999)} {random.randint(1000, 9999)}",
         "language": "ar"}
        for name, name_ar, country, city, category in EXTRA_SUPPLIER_NAMES
    ]
    suppliers: list[Supplier] = []
    for _index, definition in enumerate(supplier_defs):
        supplier = await create_supplier(
            session, tenant, name=definition["name"], name_ar=definition["name_ar"],
            country=definition["country"], city=definition["city"], website=definition["website"],
            email=definition["email"], phone=definition["phone"], language=definition.get("language", "ar"),
            categories=[definition["category"]], confidence=0.95, created_by=users["buyer"].id,
        )
        score = definition.get("score", 75)
        supplier.rating_price = Decimal(str(score - 4))
        supplier.rating_lead_time = Decimal(str(min(100, score + 3)))
        supplier.rating_quality = Decimal(str(score))
        supplier.rating_responsiveness = Decimal(str(min(100, score + 5)))
        supplier.overall_score = Decimal(str(score))
        session.add(SupplierCertification(tenant_id=tenant.id, supplier_id=supplier.id, code="ISO 9001",
                                          issuer="TÜV", expires_at=datetime.now(UTC) + timedelta(days=400)))
        suppliers.append(supplier)
    await session.flush()

    for item_key, base_price in [("Rebar 12mm B420D", 21500), ("Stretch film 500mm", 480),
                                 ("Gear oil EP 80W-90", 1650), ("Bearing SKF 6205", 420),
                                 ("MCB 32A Schneider", 640)]:
        _make_price_history(session, tenant, item_key, base_price,
                                  suppliers[random.randrange(len(suppliers))].id)

    # ------------------------------------------------ historical RFQ 1 (closed + PO)
    pr1 = await create_pr(session, tenant, lines=[
        {"description": "Rebar 12mm B420D", "quantity": "500", "uom": "t", "target_price": "21000",
         "taxonomy_code": "30102400", "category_code": "STEEL"},
        {"description": "Stretch film 500mm 23 micron", "quantity": "200", "uom": "roll",
         "target_price": "450", "taxonomy_code": "24112000", "category_code": "PACKAGING"},
    ], title="Q3 raw material replenishment", source="excel", plant_code=plants[0].code,
        requested_by=users["requester"].id, currency="EGP")
    steel_suppliers = [s for s in suppliers if "STEEL" in (s.categories or [])][:3]
    rfq1 = await create_rfq_from_pr(session, tenant, pr1, supplier_ids=[s.id for s in steel_suppliers],
                                    buyer_id=users["buyer"].id, deadline_days=7, mode="semi_auto")
    await dispatch_rfq(session, tenant, rfq1, actor_id=users["buyer"].id)
    rfq_lines1 = list((await session.execute(select(RFQLine).where(RFQLine.rfq_id == rfq1.id))).scalars().all())
    offers1 = []
    for index, supplier in enumerate(steel_suppliers):
        price = Decimal(str(21000 - index * 450))
        offer = Offer(tenant_id=tenant.id, rfq_id=rfq1.id, supplier_id=supplier.id, currency="EGP",
                      incoterm="DAP", payment_terms="Net 30", lead_time_days=14 + index * 3,
                      validity_date=datetime.now(UTC) + timedelta(days=14), status="verified",
                      confidence=Decimal("0.96"), fx_rate=Decimal("1"))
        session.add(offer)
        await session.flush()
        subtotal = Decimal("0")
        for line in rfq_lines1:
            unit = price if line.taxonomy_code == "30102400" else Decimal("455")
            total = unit * line.quantity
            session.add(OfferLine(tenant_id=tenant.id, offer_id=offer.id, rfq_line_id=line.id,
                                  description=line.description, quantity=line.quantity, uom=line.uom,
                                  unit_price=unit, currency="EGP", line_total=total, confidence=Decimal("0.95")))
            subtotal += total
        offer.subtotal = subtotal
        offer.total_base = subtotal
        offer.tco_total = subtotal
        offers1.append(offer)
    await session.flush()
    best = min(offers1, key=lambda o: o.subtotal)
    best.status = "selected"
    chain = (await session.execute(
        select(ApprovalChain).where(ApprovalChain.tenant_id == tenant.id)
    )).scalars().first()
    session.add(Approval(tenant_id=tenant.id, rfq_id=rfq1.id, offer_id=best.id,
                         chain_id=chain.id if chain else None, step_no=1,
                         approver_role="procurement_manager", approver_id=users["procurement_manager"].id,
                         status="approved", amount=best.subtotal, currency="EGP", decision="approve",
                         comment="Approved per budget", decided_at=datetime.now(UTC) - timedelta(days=12)))
    session.add(Approval(tenant_id=tenant.id, rfq_id=rfq1.id, offer_id=best.id,
                         chain_id=chain.id if chain else None, step_no=2,
                         approver_role="finance_approver", approver_id=users["finance_approver"].id,
                         status="approved", amount=best.subtotal, currency="EGP", decision="approve",
                         comment="Cash flow confirmed", decided_at=datetime.now(UTC) - timedelta(days=11)))
    rfq1.status = "closed"
    rfq1.closed_at = datetime.now(UTC) - timedelta(days=11)
    rfq1.stage_history = [
        {"stage": "draft", "at": (datetime.now(UTC) - timedelta(days=20)).isoformat(), "actor": "buyer"},
        {"stage": "sent", "at": (datetime.now(UTC) - timedelta(days=19)).isoformat(), "actor": "buyer"},
        {"stage": "replied", "at": (datetime.now(UTC) - timedelta(days=17)).isoformat(), "actor": "system"},
        {"stage": "closed", "at": (datetime.now(UTC) - timedelta(days=11)).isoformat(), "actor": "buyer"},
    ]
    po1 = await create_po_for_offer(session, tenant, rfq1, best, created_by=users["buyer"].id)
    session.add(Comment(tenant_id=tenant.id, rfq_id=rfq1.id, author_id=users["buyer"].id,
                        author_name="Omar Khaled", body="Approved and PO issued to the lowest TCO offer.",
                        mentions=[]))
    await _seed_delivery(session, tenant, po1, users)

    # ------------------------------------------------ historical RFQ 2 (closed, lubricants)
    pr2 = await create_pr(session, tenant, lines=[
        {"description": "Gear oil EP 80W-90", "quantity": "800", "uom": "l", "target_price": "1600",
         "taxonomy_code": "12180000", "category_code": "LUBRICANTS"},
    ], title="Hydraulic & gear oil - Alexandria plant", source="text", plant_code=plants[2].code,
        requested_by=users["requester"].id, currency="EGP")
    lube_suppliers = [s for s in suppliers if "LUBRICANTS" in (s.categories or [])][:2]
    rfq2 = await create_rfq_from_pr(session, tenant, pr2, supplier_ids=[s.id for s in lube_suppliers],
                                    buyer_id=users["procurement_manager"].id, deadline_days=6, mode="draft_only")
    await dispatch_rfq(session, tenant, rfq2, actor_id=users["procurement_manager"].id)
    line2 = (await session.execute(select(RFQLine).where(RFQLine.rfq_id == rfq2.id))).scalars().first()
    for index, supplier in enumerate(lube_suppliers):
        unit = Decimal(str(1580 - index * 40))
        offer = Offer(tenant_id=tenant.id, rfq_id=rfq2.id, supplier_id=supplier.id, currency="EGP",
                      incoterm="DAP", payment_terms="Net 45", lead_time_days=10,
                      status="verified", confidence=Decimal("0.94"), fx_rate=Decimal("1"))
        session.add(offer)
        await session.flush()
        session.add(OfferLine(tenant_id=tenant.id, offer_id=offer.id, rfq_line_id=line2.id,
                              description=line2.description, quantity=line2.quantity, uom=line2.uom,
                              unit_price=unit, currency="EGP", line_total=unit * line2.quantity,
                              confidence=Decimal("0.93")))
        offer.subtotal = unit * line2.quantity
        offer.total_base = offer.subtotal
        offer.tco_total = offer.subtotal
        if index == 1:
            offer.status = "selected"
    rfq2.status = "closed"
    rfq2.closed_at = datetime.now(UTC) - timedelta(days=3)
    rfq2.stage_history = [
        {"stage": "draft", "at": (datetime.now(UTC) - timedelta(days=9)).isoformat(), "actor": "manager"},
        {"stage": "sent", "at": (datetime.now(UTC) - timedelta(days=8)).isoformat(), "actor": "manager"},
        {"stage": "closed", "at": (datetime.now(UTC) - timedelta(days=3)).isoformat(), "actor": "manager"},
    ]

    # ------------------------------------------------ open RFQ mid-negotiation
    pr3 = await create_pr(session, tenant, lines=[
        {"description": "Rebar 16mm B420D", "quantity": "300", "uom": "t", "target_price": "21200",
         "taxonomy_code": "30102400", "category_code": "STEEL"},
        {"description": "Bearing SKF 6205-2RS", "quantity": "120", "uom": "pcs", "target_price": "400",
         "taxonomy_code": "31171500", "category_code": "SPARE_PARTS"},
    ], title="Q4 steel + bearings - Sadat plant", source="excel", plant_code=plants[0].code,
        requested_by=users["requester"].id, currency="EGP")
    chosen = [s for s in suppliers if "STEEL" in (s.categories or [])][:2] + \
             [s for s in suppliers if "SPARE_PARTS" in (s.categories or [])][:1]
    rfq3 = await create_rfq_from_pr(session, tenant, pr3, supplier_ids=[s.id for s in chosen],
                                    buyer_id=users["buyer"].id, deadline_days=9, mode="semi_auto")
    await dispatch_rfq(session, tenant, rfq3, actor_id=users["buyer"].id)
    await upsert_policy(session, tenant, rfq=rfq3, supplier_id=None, target_price=Decimal("20500"),
                        walkaway_price=Decimal("22500"), max_rounds=3,
                        allowed_concessions=["volume", "payment_terms", "lead_time"],
                        tone="professional", mode="semi_auto", value_threshold=Decimal("500000"),
                        currency="EGP", notes="Steel market softening; hold at or below 20,500 if possible.",
                        created_by=users["procurement_manager"].id)
    for supplier in chosen:
        invitation = (await session.execute(
            select(RFQSupplier).where(RFQSupplier.rfq_id == rfq3.id, RFQSupplier.supplier_id == supplier.id)
        )).scalar_one()
        invitation.status = "responded"
        invitation.responded_at = datetime.now(UTC) - timedelta(days=1)
    await session.flush()

    tenant.settings = {**(tenant.settings or {}), "demo_seeded": True,
                       "demo_seeded_at": datetime.now(UTC).isoformat()}
    session.add(tenant)
    await session.flush()
    return {
        "suppliers": len(suppliers), "plants": len(plants), "users": len(users) + 1,
        "rfqs": [{"code": rfq1.code, "status": rfq1.status}, {"code": rfq2.code, "status": rfq2.status},
                 {"code": rfq3.code, "status": rfq3.status, "open_for_demo": True}],
        "demo_password": demo_password,
        "categories": [c["code"] for c in CATEGORIES],
        "note": "Demo credentials are for development only; change them before any shared deployment.",
    }
