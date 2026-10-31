"""Double-entry accounting for the procurement side of the ledger (phase 2).

Design rules, and why they are what they are:

* **The books are derived from documents, never typed from a guess.** Every automatic entry is built
  from a posted PO, a confirmed goods receipt, an approved invoice and its stored match result. The
  numbers are arithmetic over those records, so an entry can always be re-derived and audited.
* **Draft -> posted -> reversed.** A draft can be edited by its creator; a posted entry is immutable
  and can only be corrected by a linked reversal (same accounts, debits and credits swapped).
* **Human gate.** Drafting needs ``accounting:write``; posting needs ``accounting:post`` and a named
  user; reversing needs ``accounting:reverse``. No agent tool can reach any of it.
* **GR/IR is a real clearing account.** Goods received are accrued at PO cost (Dr inventory /
  Cr GR/IR); the supplier invoice clears GR/IR, books VAT and variance, and creates the payable
  (Cr AP). Paying settles AP against the bank. Three entries, three human approvals.
* **Analytical accounting rides on the entry.** Each inventory/expense line carries a cost centre,
  an optional unit number and an optional item, and the posting writes a ``cost_allocation`` fact so
  cost reports never recompute from mutable documents.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entities import (
    AuditPackage,
    CostAllocation,
    CostCenter,
    GLAccount,
    GoodsReceipt,
    JournalEntry,
    JournalLine,
    PurchaseOrder,
    Supplier,
    SupplierInvoice,
    SupplierLedgerEntry,
    Tenant,
)
from app.services import numbers
from app.services.common import lock_for_update, next_code, notify
from app.services.receiving import po_lines, received_totals

log = logging.getLogger("procureai.accounting")

# Accounts the automatic postings depend on. They are seeded per tenant by bootstrap and by
# migration 0015; a tenant may rename them but the codes are the contract.
ACCOUNT_INVENTORY = "110100"
ACCOUNT_GRIR = "110200"
ACCOUNT_VAT_INPUT = "120100"
ACCOUNT_AP = "210100"
ACCOUNT_BANK = "100100"
ACCOUNT_EXPENSE_MATERIALS = "510100"
ACCOUNT_PRICE_VARIANCE = "530100"
ACCOUNT_ROUNDING = "540100"
UNALLOCATED_CENTER = "CC-000"

# Accounts that never carry an analytical dimension: control accounts, tax, bank and the two
# difference accounts. A cost report cannot attribute a price variance to a cost centre, and
# pretending otherwise would invent data.
SYSTEM_ACCOUNTS = {ACCOUNT_GRIR, ACCOUNT_VAT_INPUT, ACCOUNT_PRICE_VARIANCE, ACCOUNT_ROUNDING,
                   ACCOUNT_AP, ACCOUNT_BANK}

# The standard procurement chart, seeded per tenant (bootstrap and migration 0015). A tenant may add
# accounts freely; these codes are the ones the automatic postings depend on.
# (code, name, name_ar, kind, normal_balance, is_control)
ACCOUNTS: tuple[tuple[str, str, str, str, str, bool], ...] = (
    (ACCOUNT_INVENTORY, "Inventory — raw materials", "المخزون - مواد خام", "asset", "debit", False),
    (ACCOUNT_GRIR, "GR/IR clearing", "حساب الاستلام غير المفوتر", "asset", "debit", True),
    (ACCOUNT_VAT_INPUT, "VAT input — recoverable", "ضريبة القيمة المضافة - مشتريات", "asset", "debit", False),
    (ACCOUNT_AP, "Accounts payable — trade", "الدائنون - الموردون", "liability", "credit", True),
    ("220100", "Accrued expenses", "مصروفات مستحقة", "liability", "credit", False),
    (ACCOUNT_BANK, "Bank — operating account", "البنك - الحساب الجاري", "asset", "debit", False),
    ("510100", "Materials consumed", "تكلفة المواد المستهلكة", "expense", "debit", False),
    ("520100", "Freight and clearing", "الشحن والتخليص", "expense", "debit", False),
    (ACCOUNT_PRICE_VARIANCE, "Purchase price variance", "فرق أسعار الشراء", "expense", "debit", False),
    (ACCOUNT_ROUNDING, "Rounding differences", "فروق التقريب", "expense", "debit", False),
    ("300100", "Retained earnings", "الأرباح المحتجزة", "equity", "credit", False),
)

EPSILON = Decimal("0.01")

ENTRY_KINDS = {
    "receipt_accrual": ("Goods received — GR/IR accrual", "استلام بضاعة - استحقاق الاستلام غير المفوتر"),
    "invoice": ("Supplier invoice — GR/IR clearing and payable", "فاتورة مورد - إقفال الاستلام وإنشاء الدائن"),
    "payment": ("Payment settled outside the platform", "سداد منفذ خارج المنصة"),
    "manual": ("Manual journal entry", "قيد يدوي"),
    "reversal": ("Reversal entry", "قيد عكسي"),
}

STATUS_LABELS = {
    "draft": ("Draft", "مسودة"),
    "posted": ("Posted", "مُرحّل"),
    "reversed": ("Reversed", "معكوس"),
}


class AccountingError(ValueError):
    """A posting rule was violated; the message is safe to show to the user."""

    def __init__(self, message: str, code: str = "accounting_error", extra: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra or {}


# ------------------------------------------------------------------ chart of accounts & dimensions
async def list_accounts(session: AsyncSession, tenant_id: str, *, kind: str | None = None,
                        active_only: bool = True) -> list[GLAccount]:
    query = select(GLAccount).where(GLAccount.tenant_id == tenant_id)
    if kind:
        query = query.where(GLAccount.kind == kind)
    if active_only:
        query = query.where(GLAccount.is_active.is_(True))
    return list((await session.execute(query.order_by(GLAccount.code))).scalars().all())


async def account_map(session: AsyncSession, tenant_id: str) -> dict[str, GLAccount]:
    return {row.code: row for row in await list_accounts(session, tenant_id, active_only=False)}


async def ensure_account(session: AsyncSession, tenant: Tenant, *, code: str, name: str, name_ar: str,
                         kind: str, normal_balance: str, is_control: bool = False,
                         created_by: str | None = None) -> GLAccount:
    """Idempotent: used by bootstrap and by the API when a tenant adds an account."""
    existing = (await session.execute(
        select(GLAccount).where(GLAccount.tenant_id == tenant.id, GLAccount.code == code)
    )).scalar_one_or_none()
    if existing:
        existing.name, existing.name_ar = name, name_ar
        existing.kind, existing.normal_balance = kind, normal_balance
        return existing
    account = GLAccount(tenant_id=tenant.id, code=code, name=name, name_ar=name_ar, kind=kind,
                        normal_balance=normal_balance, is_control=is_control, is_active=True,
                        created_by=created_by)
    session.add(account)
    await session.flush()
    return account


async def create_account(session: AsyncSession, tenant: Tenant, *, code: str, name: str, name_ar: str,
                         kind: str, normal_balance: str | None = None, parent_code: str | None = None,
                         is_control: bool = False, description: str = "",
                         created_by: str | None = None) -> GLAccount:
    code = code.strip()[:20]
    if not code or not name.strip():
        raise AccountingError("An account needs a code and a name.", code="invalid_account")
    if kind not in {"asset", "liability", "equity", "revenue", "expense"}:
        raise AccountingError("kind must be asset, liability, equity, revenue or expense.",
                              code="invalid_account_kind")
    existing = (await session.execute(
        select(GLAccount).where(GLAccount.tenant_id == tenant.id, GLAccount.code == code)
    )).scalar_one_or_none()
    if existing:
        raise AccountingError(f"Account {code} already exists.", code="duplicate_account")
    normal = normal_balance or ("debit" if kind in {"asset", "expense"} else "credit")
    if normal not in {"debit", "credit"}:
        raise AccountingError("normal_balance must be debit or credit.", code="invalid_normal_balance")
    account = GLAccount(tenant_id=tenant.id, code=code, name=name.strip()[:160], name_ar=name_ar.strip()[:160],
                        kind=kind, normal_balance=normal, parent_code=parent_code, is_control=is_control,
                        description=description[:2000], created_by=created_by)
    session.add(account)
    await session.flush()
    return account


async def update_account(session: AsyncSession, tenant: Tenant, account: GLAccount, *,
                         name: str | None = None, name_ar: str | None = None, is_active: bool | None = None,
                         description: str | None = None) -> GLAccount:
    if name is not None:
        account.name = name.strip()[:160]
    if name_ar is not None:
        account.name_ar = name_ar.strip()[:160]
    if description is not None:
        account.description = description[:2000]
    if is_active is not None:
        if not is_active:
            # Deactivating an account that carries balances would orphan them: refuse.
            used = (await session.execute(
                select(func.count()).select_from(JournalLine).where(
                    JournalLine.tenant_id == tenant.id, JournalLine.account_code == account.code)
            )).scalar_one()
            if used:
                raise AccountingError(
                    f"Account {account.code} has {used} posted line(s); deactivate it only after a "
                    "closing entry moves the balance.", code="account_in_use")
        account.is_active = is_active
    await session.flush()
    return account


async def list_cost_centers(session: AsyncSession, tenant_id: str, *, active_only: bool = True) -> list[CostCenter]:
    query = select(CostCenter).where(CostCenter.tenant_id == tenant_id)
    if active_only:
        query = query.where(CostCenter.is_active.is_(True))
    return list((await session.execute(query.order_by(CostCenter.code))).scalars().all())


async def ensure_cost_center(session: AsyncSession, tenant: Tenant, *, code: str, name: str, name_ar: str,
                             plant_id: str | None = None, description: str = "",
                             created_by: str | None = None) -> CostCenter:
    existing = (await session.execute(
        select(CostCenter).where(CostCenter.tenant_id == tenant.id, CostCenter.code == code)
    )).scalar_one_or_none()
    if existing:
        return existing
    center = CostCenter(tenant_id=tenant.id, code=code, name=name, name_ar=name_ar, plant_id=plant_id,
                        is_active=True, description=description[:2000], created_by=created_by)
    session.add(center)
    await session.flush()
    return center


async def create_cost_center(session: AsyncSession, tenant: Tenant, *, code: str, name: str, name_ar: str = "",
                             plant_id: str | None = None, description: str = "",
                             created_by: str | None = None) -> CostCenter:
    code = code.strip()[:20]
    if not code or not name.strip():
        raise AccountingError("A cost centre needs a code and a name.", code="invalid_cost_center")
    existing = (await session.execute(
        select(CostCenter).where(CostCenter.tenant_id == tenant.id, CostCenter.code == code)
    )).scalar_one_or_none()
    if existing:
        raise AccountingError(f"Cost centre {code} already exists.", code="duplicate_cost_center")
    return await ensure_cost_center(session, tenant, code=code, name=name.strip()[:160],
                                    name_ar=name_ar.strip()[:160], plant_id=plant_id,
                                    description=description, created_by=created_by)


async def update_cost_center(session: AsyncSession, center: CostCenter, *, name: str | None = None,
                             name_ar: str | None = None, is_active: bool | None = None,
                             description: str | None = None, manager_id: str | None = None) -> CostCenter:
    if name is not None:
        center.name = name.strip()[:160]
    if name_ar is not None:
        center.name_ar = name_ar.strip()[:160]
    if description is not None:
        center.description = description[:2000]
    if manager_id is not None:
        center.manager_id = manager_id
    if is_active is not None:
        center.is_active = is_active
    await session.flush()
    return center


# ------------------------------------------------------------------ journal entries
def _line(line_no: int, account_code: str, description: str, *, debit: Decimal | str = "0",
          credit: Decimal | str = "0", cost_center_code: str | None = None,
          unit_number: str | None = None, item_id: str | None = None,
          supplier_id: str | None = None) -> dict:
    return {
        "line_no": line_no,
        "account_code": account_code,
        "description": description[:400],
        "debit": str(numbers.money(numbers.parse_decimal(debit) or Decimal("0"))),
        "credit": str(numbers.money(numbers.parse_decimal(credit) or Decimal("0"))),
        "cost_center_code": cost_center_code,
        "unit_number": (unit_number or None) and str(unit_number)[:40],
        "item_id": item_id,
        "supplier_id": supplier_id,
    }


def validate_lines(lines: list[dict], accounts: dict[str, GLAccount]) -> tuple[Decimal, Decimal]:
    """Every line must hit one known, active account on exactly one side; the entry must balance."""
    if not lines:
        raise AccountingError("A journal entry needs at least two lines.", code="empty_entry")
    if len(lines) < 2:
        raise AccountingError("A journal entry needs at least two lines.", code="empty_entry")
    debit_total = Decimal("0")
    credit_total = Decimal("0")
    for line in lines:
        account = accounts.get(line.get("account_code", ""))
        if not account:
            raise AccountingError(f"Account {line.get('account_code')} does not exist.",
                                  code="unknown_account", extra={"account_code": line.get("account_code")})
        if not account.is_active:
            raise AccountingError(f"Account {account.code} is inactive.",
                                  code="inactive_account", extra={"account_code": account.code})
        debit = numbers.money(numbers.parse_decimal(line.get("debit")) or Decimal("0"))
        credit = numbers.money(numbers.parse_decimal(line.get("credit")) or Decimal("0"))
        if debit < 0 or credit < 0:
            raise AccountingError("Debits and credits cannot be negative.", code="negative_amount")
        if debit > 0 and credit > 0:
            raise AccountingError("A line is either a debit or a credit, never both.",
                                  code="both_sides")
        if debit == 0 and credit == 0:
            raise AccountingError("A line needs a non-zero amount.", code="zero_line")
        debit_total += debit
        credit_total += credit
    if abs(debit_total - credit_total) > EPSILON:
        raise AccountingError(
            f"The entry is not balanced: debits {numbers.money(debit_total)} vs credits "
            f"{numbers.money(credit_total)}.", code="unbalanced",
            extra={"debit": str(numbers.money(debit_total)), "credit": str(numbers.money(credit_total))},
        )
    if debit_total == 0:
        raise AccountingError("A journal entry cannot be zero.", code="zero_entry")
    return numbers.money(debit_total), numbers.money(credit_total)


async def create_entry(session: AsyncSession, tenant: Tenant, *, entry_date: datetime | None = None,
                       currency: str | None = None, memo: str = "", memo_ar: str = "",
                       lines: list[dict], source_type: str = "manual", source_id: str | None = None,
                       po_id: str | None = None, invoice_id: str | None = None,
                       receipt_id: str | None = None, supplier_id: str | None = None,
                       match_snapshot: dict | None = None, created_by: str | None = None,
                       allocations: list[dict] | None = None) -> JournalEntry:
    """Validate and persist a *draft*. Nothing reaches the ledger until ``post_entry`` runs."""
    accounts = await account_map(session, tenant.id)
    debit, credit = validate_lines(lines, accounts)
    centers = {row.code for row in await list_cost_centers(session, tenant.id)}
    for line in lines:
        code = line.get("cost_center_code")
        if code and code not in centers:
            raise AccountingError(f"Cost centre {code} does not exist.", code="unknown_cost_center")
        account = accounts[line["account_code"]]
        line_debit = numbers.parse_decimal(line.get("debit")) or Decimal("0")
        analytical = (account.kind == "expense" or account.code == ACCOUNT_INVENTORY) \
            and account.code not in SYSTEM_ACCOUNTS
        if line_debit > 0 and analytical and not code:
            raise AccountingError(
                "A cost or inventory debit needs a cost centre so the amount stays analytical.",
                code="missing_cost_center", extra={"account_code": account.code},
            )
    entry = JournalEntry(
        tenant_id=tenant.id, number=await next_code(session, JournalEntry, tenant.id, "JE"),
        entry_date=entry_date or datetime.now(UTC), currency=(currency or tenant.currency).upper()[:3],
        memo=memo[:400], memo_ar=memo_ar[:400], status="draft", source_type=source_type,
        source_id=source_id, po_id=po_id, invoice_id=invoice_id, receipt_id=receipt_id,
        supplier_id=supplier_id, match_snapshot=match_snapshot or {}, total_debit=debit,
        total_credit=credit, lines=lines, created_by=created_by,
    )
    session.add(entry)
    await session.flush()
    for item in allocations or []:
        session.add(CostAllocation(
            tenant_id=tenant.id, journal_entry_id=entry.id, po_id=po_id, invoice_id=invoice_id,
            receipt_id=receipt_id, line_no=int(item.get("line_no") or 1),
            description=str(item.get("description") or "")[:400],
            cost_center_code=str(item.get("cost_center_code") or UNALLOCATED_CENTER)[:20],
            unit_number=(item.get("unit_number") or None) and str(item.get("unit_number"))[:40],
            item_id=item.get("item_id"), amount=numbers.money(numbers.parse_decimal(item.get("amount")) or Decimal("0")),
            quantity=numbers.parse_decimal(item.get("quantity")) or Decimal("0"),
            currency=entry.currency, entry_date=entry.entry_date,
        ))
    await session.flush()
    return entry


async def post_entry(session: AsyncSession, tenant: Tenant, entry: JournalEntry, *,
                     posted_by: str | None, posted_at: datetime | None = None,
                     allow_own_draft: bool = False) -> dict:
    """Post a draft. Segregation of duties: the poster may not be the drafter unless overridden."""
    locked = await lock_for_update(session, JournalEntry, entry.id, tenant_id=tenant.id)
    if locked is not None:
        entry = locked
    if entry.status == "posted":
        raise AccountingError(f"Entry {entry.number} is already posted.", code="already_posted")
    if entry.status == "reversed":
        raise AccountingError(f"Entry {entry.number} has been reversed.", code="already_reversed")
    if not allow_own_draft and entry.created_by and posted_by and entry.created_by == posted_by:
        raise AccountingError(
            "A journal entry must be posted by someone other than its author.",
            code="segregation_of_duties",
        )
    accounts = await account_map(session, tenant.id)
    debit, credit = validate_lines(entry.lines or [], accounts)
    entry.total_debit, entry.total_credit = debit, credit
    entry.status = "posted"
    entry.posted_by = posted_by
    entry.posted_at = posted_at or datetime.now(UTC)
    await session.flush()
    for line in entry.lines or []:
        session.add(JournalLine(
            tenant_id=tenant.id, entry_id=entry.id, line_no=int(line.get("line_no") or 1),
            account_code=line["account_code"], description=line.get("description", ""),
            debit=numbers.parse_decimal(line.get("debit")) or Decimal("0"),
            credit=numbers.parse_decimal(line.get("credit")) or Decimal("0"),
            cost_center_code=line.get("cost_center_code"), unit_number=line.get("unit_number"),
            item_id=line.get("item_id"), supplier_id=line.get("supplier_id") or entry.supplier_id,
        ))
    await session.flush()
    return {"ok": True, "entry_id": entry.id, "number": entry.number, "status": entry.status,
            "total_debit": str(entry.total_debit), "total_credit": str(entry.total_credit)}


async def reverse_entry(session: AsyncSession, tenant: Tenant, entry: JournalEntry, *,
                        reversed_by: str | None, reason: str) -> JournalEntry:
    """Create the mirror entry. The original stays posted — history is never edited."""
    if entry.status != "posted":
        raise AccountingError(
            f"Only a posted entry can be reversed ({entry.number} is {entry.status}).",
            code="not_posted",
        )
    if not reason.strip():
        raise AccountingError("A reversal needs a written reason.", code="reason_required")
    if entry.reversed_by:
        raise AccountingError(f"Entry {entry.number} is already reversed.", code="already_reversed")

    mirrored = [
        {**line, "debit": line.get("credit", "0"), "credit": line.get("debit", "0")}
        for line in (entry.lines or [])
    ]
    reversal = await create_entry(
        session, tenant, entry_date=datetime.now(UTC), currency=entry.currency,
        memo=f"Reversal of {entry.number}", memo_ar=f"عكس القيد {entry.number}",
        lines=mirrored, source_type="reversal", source_id=entry.id, po_id=entry.po_id,
        invoice_id=entry.invoice_id, receipt_id=entry.receipt_id, supplier_id=entry.supplier_id,
        match_snapshot={"reversal_of": entry.number}, created_by=reversed_by,
    )
    reversal.reversal_of = entry.id
    reversal.reversal_reason = reason.strip()[:2000]
    await session.flush()
    await post_entry(session, tenant, reversal, posted_by=reversed_by, allow_own_draft=True)
    entry.status = "reversed"
    entry.reversed_by = reversal.id
    await session.flush()
    await notify(session, tenant_id=tenant.id, user_id=entry.created_by, kind="journal_reversed",
                 title=f"Journal entry {entry.number} reversed",
                 title_ar=f"تم عكس القيد {entry.number}",
                 body=reason[:300], link="/accounting/journal", severity="warning")
    return reversal


# ------------------------------------------------------------------ posting from purchase documents
async def _confirmed_receipt_value(session: AsyncSession, tenant_id: str, po: PurchaseOrder) -> dict:
    """Accepted quantity per line and its value at PO price (the GR/IR accrual basis)."""
    totals = await received_totals(session, tenant_id, po.id)
    rows: list[dict] = []
    total = Decimal("0")
    for line in po_lines(po):
        accepted = totals.get(line["line_no"], Decimal("0"))
        if accepted <= 0:
            continue
        amount = numbers.money(accepted * (line["unit_price"] or Decimal("0")))
        total += amount
        rows.append({"line_no": line["line_no"], "description": line.get("description", ""),
                     "quantity": accepted, "unit_price": line["unit_price"] or Decimal("0"),
                     "amount": amount, "uom": line.get("uom", "")})
    return {"rows": rows, "total": numbers.money(total)}


def _allocation_index(allocations: list[dict] | None) -> dict[int, dict]:
    index: dict[int, dict] = {}
    for row in allocations or []:
        index[int(row.get("line_no") or 0)] = row
    return index


async def _validate_allocation(session: AsyncSession, tenant: Tenant, row: dict | None,
                               line_no: int) -> dict:
    """Return a normalised allocation, defaulting to the unallocated cost centre."""
    code = (row or {}).get("cost_center_code") or UNALLOCATED_CENTER
    centers = {center.code for center in await list_cost_centers(session, tenant.id)}
    if code not in centers:
        raise AccountingError(f"Cost centre {code} does not exist for line {line_no}.",
                              code="unknown_cost_center", extra={"line_no": line_no})
    unit = (row or {}).get("unit_number")
    if unit and (len(str(unit)) > 40 or not str(unit).strip()):
        raise AccountingError(f"Unit number on line {line_no} is invalid.", code="invalid_unit_number",
                              extra={"line_no": line_no})
    return {"cost_center_code": code, "unit_number": (str(unit).strip() if unit else None),
            "item_id": (row or {}).get("item_id") or None}


async def _accrued_by_line(session: AsyncSession, tenant_id: str, po_id: str) -> dict[int, Decimal]:
    """Value already accrued per PO line, counting drafts as well as postings (no double accrual)."""
    entries = list((await session.execute(
        select(JournalEntry).where(JournalEntry.tenant_id == tenant_id, JournalEntry.po_id == po_id,
                                   JournalEntry.source_type == "receipt_accrual",
                                   JournalEntry.status.in_(["draft", "posted"]))
    )).scalars().all())
    accrued: dict[int, Decimal] = {}
    for entry in entries:
        for line in entry.lines or []:
            if line.get("account_code") != ACCOUNT_INVENTORY:
                continue
            line_no = int(line.get("po_line_no") or 0)
            accrued[line_no] = accrued.get(line_no, Decimal("0")) + (
                numbers.parse_decimal(line.get("debit")) or Decimal("0"))
    return accrued


async def draft_receipt_accrual(session: AsyncSession, tenant: Tenant, po: PurchaseOrder, *,
                                allocations: list[dict] | None = None, created_by: str | None = None,
                                entry_date: datetime | None = None) -> JournalEntry:
    """Dr Inventory / Cr GR/IR for what was confirmed and not accrued yet, at PO cost.

    Partial receipts are the norm, so the accrual covers only the *increment*: the value of the
    confirmed quantity minus what previous accruals already booked for that line.
    """
    value = await _confirmed_receipt_value(session, tenant.id, po)
    if not value["rows"]:
        raise AccountingError(
            "Nothing is confirmed as received yet, so there is nothing to accrue.",
            code="no_confirmed_receipt",
        )
    accrued = await _accrued_by_line(session, tenant.id, po.id)
    allocated = _allocation_index(allocations)
    lines: list[dict] = []
    cost_facts: list[dict] = []
    delta_total = Decimal("0")
    for row in value["rows"]:
        already = accrued.get(row["line_no"], Decimal("0"))
        delta = numbers.money(row["amount"] - already)
        if delta <= EPSILON:
            continue
        alloc = await _validate_allocation(session, tenant, allocated.get(row["line_no"]), row["line_no"])
        line = _line(len(lines) + 1, ACCOUNT_INVENTORY, f"Received: {row['description']}", debit=delta,
                     cost_center_code=alloc["cost_center_code"], unit_number=alloc["unit_number"],
                     item_id=alloc["item_id"])
        line["po_line_no"] = row["line_no"]
        lines.append(line)
        cost_facts.append({**alloc, "line_no": row["line_no"], "description": row["description"],
                           "amount": str(delta), "quantity": str(row["quantity"])})
        delta_total += delta
    if not lines:
        raise AccountingError(
            "Every confirmed quantity on this order is already accrued.",
            code="duplicate_accrual",
        )
    lines.append(_line(len(lines) + 1, ACCOUNT_GRIR,
                       f"GR/IR clearing for {po.code}", credit=delta_total))
    receipt = (await session.execute(
        select(GoodsReceipt).where(GoodsReceipt.tenant_id == tenant.id, GoodsReceipt.po_id == po.id,
                                   GoodsReceipt.status == "confirmed")
        .order_by(GoodsReceipt.created_at.desc())
    )).scalars().first()
    memo_en, memo_ar = ENTRY_KINDS["receipt_accrual"]
    entry = await create_entry(
        session, tenant, entry_date=entry_date, currency=po.currency, memo=f"{memo_en} — {po.code}",
        memo_ar=f"{memo_ar} — {po.code}", lines=lines, source_type="receipt_accrual", source_id=po.id,
        po_id=po.id, receipt_id=receipt.id if receipt else None, supplier_id=po.supplier_id,
        created_by=created_by, allocations=cost_facts,
    )
    if receipt:
        entry.match_snapshot = {"receipt_code": receipt.code, "value": str(delta_total)}
    await session.flush()
    return entry


async def draft_invoice_posting(session: AsyncSession, tenant: Tenant, po: PurchaseOrder,
                                invoice: SupplierInvoice, *, allocations: list[dict] | None = None,
                                created_by: str | None = None,
                                entry_date: datetime | None = None) -> JournalEntry:
    """Clear GR/IR, book VAT and price variance, and create the payable for an approved invoice."""
    if invoice.status not in {"approved", "partially_paid", "paid"}:
        raise AccountingError(
            f"Invoice {invoice.number} is {invoice.status}; approve it before posting to the accounts.",
            code="invoice_not_approved",
        )
    if not invoice.approved_at:
        raise AccountingError("The invoice has no recorded approval.", code="invoice_not_approved")
    existing = (await session.execute(
        select(JournalEntry).where(JournalEntry.tenant_id == tenant.id,
                                   JournalEntry.invoice_id == invoice.id,
                                   JournalEntry.source_type == "invoice",
                                   JournalEntry.status.in_(["draft", "posted"]))
    )).scalars().first()
    if existing:
        raise AccountingError(
            f"Invoice {invoice.number} is already posted as {existing.number}.",
            code="duplicate_posting", extra={"entry_id": existing.id, "number": existing.number},
        )

    await _require_audit_review(session, tenant, po)
    match = invoice.match_result or {}
    if match.get("status") != "matched":
        raise AccountingError(
            "Only a clean three-way match can be posted; re-run the match or correct the invoice.",
            code="match_not_clean", extra={"match_status": match.get("status")},
        )

    ordered = {row["line_no"]: row for row in po_lines(po)}
    allocated = _allocation_index(allocations)
    # Was the receipt already accrued? Then the invoice clears GR/IR; otherwise the invoice is the
    # first recognition of the cost and must hit inventory (or expense) with its cost centre. The
    # check is on posted entries, so the two paths can never double-count the same goods.
    accrual_posted = (await session.execute(
        select(JournalEntry.id).where(JournalEntry.tenant_id == tenant.id, JournalEntry.po_id == po.id,
                                      JournalEntry.source_type == "receipt_accrual",
                                      JournalEntry.status == "posted").limit(1)
    )).scalar_one_or_none() is not None

    lines: list[dict] = []
    cost_facts: list[dict] = []
    clearing_total = Decimal("0")
    variance_net = Decimal("0")
    for index, raw in enumerate(invoice.lines or [], start=1):
        line_no = int(raw.get("line_no") or index)
        quantity = numbers.parse_decimal(raw.get("quantity")) or Decimal("0")
        unit_price = numbers.parse_decimal(raw.get("unit_price")) or Decimal("0")
        po_price = (ordered.get(line_no) or {}).get("unit_price") or Decimal("0")
        clearing = numbers.money(quantity * po_price)
        invoiced = numbers.money(quantity * unit_price)
        variance = numbers.money(invoiced - clearing)
        clearing_total += clearing
        variance_net += variance
        alloc = await _validate_allocation(session, tenant, allocated.get(line_no), line_no)
        if not accrual_posted and clearing > 0:
            line = _line(len(lines) + 1, ACCOUNT_INVENTORY,
                         f"Invoice {invoice.number}: {raw.get('description', '')}",
                         debit=clearing, cost_center_code=alloc["cost_center_code"],
                         unit_number=alloc["unit_number"], item_id=alloc["item_id"])
            line["po_line_no"] = line_no
            lines.append(line)
            cost_facts.append({**alloc, "line_no": line_no,
                               "description": str(raw.get("description") or ""),
                               "amount": str(clearing), "quantity": str(quantity)})

    if accrual_posted and clearing_total > 0:
        lines.append(_line(len(lines) + 1, ACCOUNT_GRIR,
                           f"Clear GR/IR — {invoice.number}", debit=clearing_total))
    if variance_net > 0:
        lines.append(_line(len(lines) + 1, ACCOUNT_PRICE_VARIANCE,
                           f"Unfavourable price variance — {invoice.number}", debit=variance_net))
    elif variance_net < 0:
        lines.append(_line(len(lines) + 1, ACCOUNT_PRICE_VARIANCE,
                           f"Favourable price variance — {invoice.number}", credit=-variance_net))
    tax = numbers.money(numbers.parse_decimal(invoice.tax_amount) or Decimal("0"))
    if tax > 0:
        lines.append(_line(len(lines) + 1, ACCOUNT_VAT_INPUT, f"VAT input — {invoice.number}", debit=tax))
    lines.append(_line(len(lines) + 1, ACCOUNT_AP, f"Payable to supplier — {invoice.number}",
                       credit=invoice.total, supplier_id=invoice.supplier_id))
    debit_sum = sum((numbers.parse_decimal(row["debit"]) or Decimal("0") for row in lines), Decimal("0"))
    credit_sum = sum((numbers.parse_decimal(row["credit"]) or Decimal("0") for row in lines), Decimal("0"))
    gap = numbers.money(debit_sum - credit_sum)
    if abs(gap) > Decimal("0.0001"):
        if abs(gap) > Decimal("1"):
            raise AccountingError(
                f"The invoice lines do not reconcile with its total ({gap}); re-run the match.",
                code="posting_unbalanced", extra={"difference": str(gap)},
            )
        lines.append(_line(len(lines) + 1, ACCOUNT_ROUNDING, f"Rounding — {invoice.number}",
                           debit=-gap if gap < 0 else "0", credit=gap if gap > 0 else "0"))

    memo_en, memo_ar = ENTRY_KINDS["invoice"]
    entry = await create_entry(
        session, tenant, entry_date=entry_date or invoice.approved_at, currency=invoice.currency,
        memo=f"{memo_en} — {invoice.number}", memo_ar=f"{memo_ar} — {invoice.number}", lines=lines,
        source_type="invoice", source_id=invoice.id, po_id=po.id, invoice_id=invoice.id,
        supplier_id=invoice.supplier_id, created_by=created_by, allocations=cost_facts,
        match_snapshot={"status": match.get("status"), "issues": match.get("issues", []),
                        "totals": match.get("totals", {}), "checked_at": match.get("checked_at")},
    )
    return entry


async def _require_audit_review(session: AsyncSession, tenant: Tenant, po: PurchaseOrder) -> None:
    """The payable may only reach the ledger once the auditor accepted the evidence package.

    A tenant can switch this off in settings (``accounting_require_audit_review = false``) for a
    small operation with no separate auditor, but the default is on: it is what makes the
    "Ready for audit -> Posted to accounts" order in the stage machine real rather than decorative.
    """
    if (tenant.settings or {}).get("accounting_require_audit_review") is False:
        return
    package = (await session.execute(
        select(AuditPackage).where(AuditPackage.tenant_id == tenant.id, AuditPackage.po_id == po.id)
        .order_by(AuditPackage.created_at.desc())
    )).scalars().first()
    if not package:
        raise AccountingError(
            "Assemble and get the auditor's acceptance on the document package before posting the "
            "invoice to the accounts.", code="audit_review_required")
    if package.status != "reviewed" or package.decision != "accepted":
        raise AccountingError(
            f"The audit package {package.code} is '{package.status}'; it must be reviewed and accepted "
            "before the invoice is posted.", code="audit_review_required",
            extra={"package_id": package.id, "package_status": package.status})


async def draft_payment_posting(session: AsyncSession, tenant: Tenant, po: PurchaseOrder,
                                invoice: SupplierInvoice, *, amount: Decimal | str | None = None,
                                created_by: str | None = None,
                                entry_date: datetime | None = None) -> JournalEntry:
    """Dr AP / Cr Bank for a payment booked through ``record_payment`` (money already moved)."""
    paid = numbers.money(numbers.parse_decimal(invoice.paid_amount) or Decimal("0"))
    value = numbers.money(numbers.parse_decimal(amount) or paid)
    if value <= 0:
        raise AccountingError("There is no payment to post for this invoice.", code="no_payment")
    if value > paid + EPSILON:
        raise AccountingError(
            f"Cannot post {value}: only {paid} is recorded as paid on {invoice.number}.",
            code="payment_exceeds_recorded",
        )
    existing_total = (await session.execute(
        select(func.coalesce(func.sum(JournalEntry.total_debit), 0)).where(
            JournalEntry.tenant_id == tenant.id, JournalEntry.invoice_id == invoice.id,
            JournalEntry.source_type == "payment", JournalEntry.status.in_(["draft", "posted"]))
    )).scalar_one() or 0
    remaining = numbers.money(paid - Decimal(str(existing_total)))
    if remaining <= EPSILON:
        raise AccountingError(f"Every payment on {invoice.number} is already posted.",
                              code="payment_already_posted")
    value = min(value, remaining)
    lines = [
        _line(1, ACCOUNT_AP, f"Settle payable — {invoice.number}", debit=value,
              supplier_id=invoice.supplier_id),
        _line(2, ACCOUNT_BANK, f"Bank payment — {invoice.number}", credit=value),
    ]
    memo_en, memo_ar = ENTRY_KINDS["payment"]
    return await create_entry(
        session, tenant, entry_date=entry_date or datetime.now(UTC), currency=invoice.currency,
        memo=f"{memo_en} — {invoice.number}", memo_ar=f"{memo_ar} — {invoice.number}", lines=lines,
        source_type="payment", source_id=invoice.id, po_id=po.id, invoice_id=invoice.id,
        supplier_id=invoice.supplier_id, created_by=created_by,
    )


# ------------------------------------------------------------------ reporting
def entry_view(entry: JournalEntry, *, account_names: dict[str, str] | None = None) -> dict:
    label_en, label_ar = STATUS_LABELS.get(entry.status, (entry.status, entry.status))
    kind_en, kind_ar = ENTRY_KINDS.get(entry.source_type, (entry.source_type, entry.source_type))
    names = account_names or {}
    return {
        "id": entry.id, "number": entry.number, "entry_date": entry.entry_date,
        "currency": entry.currency, "memo": entry.memo, "memo_ar": entry.memo_ar,
        "status": entry.status, "status_label": label_en, "status_label_ar": label_ar,
        "source_type": entry.source_type, "source_label": kind_en, "source_label_ar": kind_ar,
        "source_id": entry.source_id, "po_id": entry.po_id, "invoice_id": entry.invoice_id,
        "receipt_id": entry.receipt_id, "supplier_id": entry.supplier_id,
        "total_debit": str(entry.total_debit), "total_credit": str(entry.total_credit),
        "balanced": abs(Decimal(str(entry.total_debit)) - Decimal(str(entry.total_credit))) <= EPSILON,
        "created_by": entry.created_by, "created_at": entry.created_at,
        "posted_by": entry.posted_by, "posted_at": entry.posted_at,
        "reversal_of": entry.reversal_of, "reversed_by": entry.reversed_by,
        "reversal_reason": entry.reversal_reason,
        "lines": [
            {**line, "account_name": names.get(line.get("account_code", ""), "")}
            for line in (entry.lines or [])
        ],
    }


async def list_entries(session: AsyncSession, tenant_id: str, *, status: str | None = None,
                       po_id: str | None = None, invoice_id: str | None = None,
                       source_type: str | None = None, limit: int = 100) -> list[JournalEntry]:
    query = select(JournalEntry).where(JournalEntry.tenant_id == tenant_id)
    if status:
        query = query.where(JournalEntry.status == status)
    if po_id:
        query = query.where(JournalEntry.po_id == po_id)
    if invoice_id:
        query = query.where(JournalEntry.invoice_id == invoice_id)
    if source_type:
        query = query.where(JournalEntry.source_type == source_type)
    return list((await session.execute(
        query.order_by(JournalEntry.entry_date.desc(), JournalEntry.created_at.desc()).limit(min(limit, 500))
    )).scalars().all())


async def trial_balance(session: AsyncSession, tenant_id: str, *, currency: str | None = None) -> dict:
    """Debits and credits per account over *posted* lines only; the proof that the books balance."""
    query = (
        select(JournalLine.account_code,
               func.coalesce(func.sum(JournalLine.debit), 0).label("debit"),
               func.coalesce(func.sum(JournalLine.credit), 0).label("credit"))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        # A reversed entry still stands in the ledger until its mirror is posted, so both are summed.
        .where(JournalEntry.tenant_id == tenant_id, JournalEntry.status.in_(["posted", "reversed"]))
        .group_by(JournalLine.account_code)
    )
    rows = (await session.execute(query)).all()
    accounts = {row.code: row for row in await list_accounts(session, tenant_id, active_only=False)}
    items: list[dict] = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for code, debit, credit in rows:
        debit = numbers.money(debit or 0)
        credit = numbers.money(credit or 0)
        account = accounts.get(code)
        total_debit += debit
        total_credit += credit
        items.append({
            "code": code,
            "account_code": code,
            "account_name": account.name if account else "",
            "account_name_ar": account.name_ar if account else "",
            "kind": account.kind if account else "",
            "debit": str(debit), "credit": str(credit),
            "balance": str(numbers.money(debit - credit)),
        })
    items.sort(key=lambda row: row["account_code"])
    return {
        "items": items,
        "total_debit": str(numbers.money(total_debit)),
        "total_credit": str(numbers.money(total_credit)),
        "balanced": abs(total_debit - total_credit) <= EPSILON,
        "currency": currency or "",
    }


async def cost_report(session: AsyncSession, tenant_id: str, *, group_by: str = "cost_center",
                      po_id: str | None = None) -> dict:
    """Posted analytical facts grouped by cost centre, unit, item, supplier or purchase order."""
    if group_by not in {"cost_center", "item", "unit", "supplier", "po"}:
        raise AccountingError("group_by must be cost_center, item, unit, supplier or po.",
                              code="invalid_group_by")
    query = (select(CostAllocation).join(JournalEntry, JournalEntry.id == CostAllocation.journal_entry_id)
             .where(JournalEntry.tenant_id == tenant_id, JournalEntry.status == "posted"))
    if po_id:
        query = query.where(CostAllocation.po_id == po_id)
    rows = list((await session.execute(query)).scalars().all())

    supplier_of: dict[str, str] = {}
    if group_by == "supplier" and rows:
        entries = list((await session.execute(
            select(JournalEntry).where(JournalEntry.id.in_({row.journal_entry_id for row in rows}))
        )).scalars().all())
        supplier_of = {entry.id: entry.supplier_id or "" for entry in entries}

    attribute = {"cost_center": "cost_center_code", "item": "item_id", "unit": "unit_number",
                 "po": "po_id"}.get(group_by)
    buckets: dict[str, Decimal] = {}
    for row in rows:
        if group_by == "supplier":
            bucket = supplier_of.get(row.journal_entry_id, "")
        else:
            bucket = getattr(row, attribute) or ""
        buckets[bucket] = buckets.get(bucket, Decimal("0")) + Decimal(str(row.amount))

    labels: dict[str, tuple[str, str]] = {}
    centers = {center.code: center for center in
               await list_cost_centers(session, tenant_id, active_only=False)}
    if group_by == "cost_center":
        labels = {code: (center.name, center.name_ar) for code, center in centers.items()}
    elif group_by == "supplier" and buckets:
        suppliers = {row.id: row for row in (await session.execute(
            select(Supplier).where(Supplier.id.in_([key for key in buckets if key])))).scalars().all()}
        labels = {key: ((supplier.name if supplier else key), (supplier.name_ar if supplier else "") or "")
                  for key, supplier in suppliers.items()}
    elif group_by == "item" and buckets:
        from app.db.models.entities import Item  # local import: only the item report needs it

        items = {row.id: row for row in (await session.execute(
            select(Item).where(Item.id.in_([key for key in buckets if key])))).scalars().all()}
        labels = {key: (row.name, row.name_ar or "") for key, row in items.items()}
    elif group_by == "po" and buckets:
        orders = {row.id: row for row in (await session.execute(
            select(PurchaseOrder).where(PurchaseOrder.id.in_([key for key in buckets if key])))).scalars().all()}
        labels = {key: (row.code, row.code) for key, row in orders.items()}

    total = sum(buckets.values(), Decimal("0"))
    items = [{
        "key": bucket,
        "label": labels.get(bucket, (bucket or "—", ""))[0],
        "label_ar": labels.get(bucket, (bucket or "—", ""))[1],
        "amount": str(numbers.money(amount)),
        "share_pct": round(float(amount / total * 100), 2) if total else 0.0,
    } for bucket, amount in buckets.items()]
    items.sort(key=lambda row: Decimal(row["amount"]), reverse=True)
    return {"group_by": group_by, "items": items, "total": str(numbers.money(total)),
            "currency": (rows[0].currency if rows else "")}


async def supplier_account(session: AsyncSession, tenant_id: str, supplier_id: str) -> dict:
    """The supplier sub-ledger: invoices, payments, notes and their journal support."""
    entries = list((await session.execute(
        select(JournalEntry).where(JournalEntry.tenant_id == tenant_id,
                                   JournalEntry.supplier_id == supplier_id,
                                   JournalEntry.status == "posted")
        .order_by(JournalEntry.entry_date)
    )).scalars().all())
    movements: list[dict] = []
    debit = Decimal("0")
    credit = Decimal("0")
    for entry in entries:
        for line in entry.lines or []:
            if line.get("account_code") != ACCOUNT_AP:
                continue
            d = numbers.parse_decimal(line.get("debit")) or Decimal("0")
            c = numbers.parse_decimal(line.get("credit")) or Decimal("0")
            debit += d
            credit += c
            source_en, source_ar = ENTRY_KINDS.get(entry.source_type,
                                                    (entry.source_type, entry.source_type))
            movements.append({
                "entry_id": entry.id, "number": entry.number, "entry_date": entry.entry_date,
                "source_type": entry.source_type,
                "source_label": source_en, "source_label_ar": source_ar,
                "description": line.get("description", ""),
                "debit": str(d), "credit": str(c), "invoice_id": entry.invoice_id,
            })
    ledger = list((await session.execute(
        select(SupplierLedgerEntry).where(SupplierLedgerEntry.tenant_id == tenant_id,
                                          SupplierLedgerEntry.supplier_id == supplier_id)
        .order_by(SupplierLedgerEntry.entry_date)
    )).scalars().all())
    supplier = (await session.execute(select(Supplier).where(Supplier.id == supplier_id))).scalar_one_or_none()
    return {
        "supplier_id": supplier_id,
        "supplier_name": supplier.name if supplier else supplier_id,
        "supplier_name_ar": (supplier.name_ar if supplier else "") or "",
        # A credit balance in AP is what the tenant owes; the sign follows debit - credit.
        "payable": str(numbers.money(credit - debit)),
        "debit_total": str(numbers.money(debit)),
        "credit_total": str(numbers.money(credit)),
        "movements": movements,
        "ledger_entries": [{
            "id": row.id, "kind": row.kind, "amount": str(row.amount), "currency": row.currency,
            "entry_date": row.entry_date, "method": row.method, "reference": row.reference,
            "invoice_id": row.invoice_id, "po_id": row.po_id,
        } for row in ledger],
    }


async def postings_for_po(session: AsyncSession, tenant_id: str, po_id: str) -> list[JournalEntry]:
    return await list_entries(session, tenant_id, po_id=po_id, limit=200)


def fingerprint(payload: dict) -> str:
    """Stable hash used by the audit package manifest (order-independent JSON)."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
