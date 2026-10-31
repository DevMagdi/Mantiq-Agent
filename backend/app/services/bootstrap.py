"""Reference data + mandatory bootstrap users (Sections 4.3/0012 and 5)."""
from __future__ import annotations

import logging
import os
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import REPO_DIR, settings
from app.core.rbac import ROLE_PERMISSIONS, role_definitions
from app.core.security import hash_password, password_policy_errors, random_password
from app.db.base import utcnow
from app.db.models.entities import (
    ApprovalChain,
    ApprovalLimit,
    ApprovalStep,
    Category,
    CostCenter,
    CountryCallPolicy,
    GLAccount,
    MessageTemplate,
    Plan,
    Role,
    RolePermission,
    Subscription,
    TaxonomyNode,
    Tenant,
    UnitConversion,
    User,
)
from app.services.catalog_data import (
    CATEGORIES,
    COUNTRY_CALL_POLICIES,
    DEFAULT_TEMPLATES,
    PLANS,
    TAXONOMY,
    UNIT_CONVERSIONS,
)

log = logging.getLogger("procureai.bootstrap")

#: Template code -> kind, so the library can group invitations, reminders, negotiation and scripts.
TEMPLATE_KINDS = {
    "rfq_invitation": "invitation",
    "reminder": "reminder",
    "counter_offer": "negotiation",
    "quote_request": "quote_request",
    "missing_info": "clarification",
    "clarification": "clarification",
    "voice_script": "voice_script",
}


async def seed_reference_data(session: AsyncSession) -> dict:
    """Idempotent reference data: roles, permissions, plans, taxonomy, units, templates, call policy."""
    created = {"roles": 0, "plans": 0, "taxonomy": 0, "units": 0, "templates": 0, "call_policies": 0}

    for definition in role_definitions():
        role = (await session.execute(select(Role).where(Role.code == definition.code))).scalar_one_or_none()
        if not role:
            session.add(Role(code=definition.code, name=definition.name, name_ar=definition.name_ar,
                             permissions=sorted(ROLE_PERMISSIONS[definition.code])))
            created["roles"] += 1
        else:
            role.name, role.name_ar = definition.name, definition.name_ar
            role.permissions = sorted(ROLE_PERMISSIONS[definition.code])
    for role_code, permissions in ROLE_PERMISSIONS.items():
        existing = {row.permission for row in (await session.execute(
            select(RolePermission).where(RolePermission.role_code == role_code)
        )).scalars().all()}
        for permission in permissions:
            if permission not in existing:
                session.add(RolePermission(role_code=role_code, permission=permission))

    for plan_def in PLANS:
        plan = (await session.execute(select(Plan).where(Plan.code == plan_def["code"]))).scalar_one_or_none()
        if not plan:
            session.add(Plan(code=plan_def["code"], name=plan_def["name"],
                             price_monthly=Decimal(plan_def["price_monthly"]), currency=plan_def["currency"],
                             limits=plan_def["limits"], features=plan_def["features"]))
            created["plans"] += 1

    for node in TAXONOMY:
        exists = (await session.execute(select(TaxonomyNode).where(TaxonomyNode.code == node["code"]))).scalar_one_or_none()
        if not exists:
            session.add(TaxonomyNode(**node))
            created["taxonomy"] += 1

    for from_unit, to_unit, factor, dimension in UNIT_CONVERSIONS:
        exists = (await session.execute(
            select(UnitConversion).where(UnitConversion.from_unit == from_unit, UnitConversion.to_unit == to_unit)
        )).scalar_one_or_none()
        if not exists:
            session.add(UnitConversion(from_unit=from_unit, to_unit=to_unit, factor=Decimal(factor),
                                       dimension=dimension))
            created["units"] += 1

    for policy in COUNTRY_CALL_POLICIES:
        exists = (await session.execute(
            select(CountryCallPolicy).where(CountryCallPolicy.country == policy["country"])
        )).scalar_one_or_none()
        if not exists:
            session.add(CountryCallPolicy(**policy))
            created["call_policies"] += 1

    await session.flush()
    return created


async def seed_tenant_defaults(session: AsyncSession, tenant: Tenant) -> dict:
    """Per-tenant defaults: categories, AR/EN templates, approval chain and limits."""
    created = {"categories": 0, "templates": 0, "chains": 0, "limits": 0, "subscription": False}
    for category in CATEGORIES:
        exists = (await session.execute(
            select(Category).where(Category.tenant_id == tenant.id, Category.code == category["code"])
        )).scalar_one_or_none()
        if not exists:
            session.add(Category(tenant_id=tenant.id, code=category["code"], name=category["name"],
                                 name_ar=category["name_ar"], taxonomy_code=category["taxonomy_code"],
                                 default_weights=category["weights"]))
            created["categories"] += 1

    for template in DEFAULT_TEMPLATES:
        exists = (await session.execute(
            select(MessageTemplate).where(MessageTemplate.tenant_id == tenant.id,
                                          MessageTemplate.code == template["code"],
                                          MessageTemplate.channel == template["channel"],
                                          MessageTemplate.locale == template["locale"])
        )).scalar_one_or_none()
        if not exists:
            from app.services.messaging import template_variables

            session.add(MessageTemplate(
                tenant_id=tenant.id, code=template["code"], channel=template["channel"],
                locale=template["locale"], name=template["name"],
                subject=template.get("subject", ""), body=template["body"],
                whatsapp_template_name=template.get("whatsapp_template_name", ""),
                kind=TEMPLATE_KINDS.get(template["code"], "invitation"),
                description=f"{template['name']} · {template['channel']}/{template['locale']}",
                variables=template_variables(template["body"]),
            ))
            created["templates"] += 1

    chain = (await session.execute(
        select(ApprovalChain).where(ApprovalChain.tenant_id == tenant.id)
    )).scalars().first()
    if not chain:
        chain = ApprovalChain(tenant_id=tenant.id, name="Standard two-level approval", min_amount=Decimal("0"),
                              max_amount=None, currency=tenant.currency, is_active=True)
        session.add(chain)
        await session.flush()
        session.add_all([
            ApprovalStep(tenant_id=tenant.id, chain_id=chain.id, step_no=1, role_code="procurement_manager",
                         threshold_amount=Decimal("0"), sla_hours=24),
            ApprovalStep(tenant_id=tenant.id, chain_id=chain.id, step_no=2, role_code="finance_approver",
                         threshold_amount=Decimal("250000"), sla_hours=48),
        ])
        created["chains"] += 1

    # High-value band: the auditor joins the chain, which is how "auditor involvement" is expressed
    # without inventing a second approval engine.
    audit_chain = (await session.execute(
        select(ApprovalChain).where(ApprovalChain.tenant_id == tenant.id,
                                    ApprovalChain.name == "High value with audit")
    )).scalars().first()
    if not audit_chain:
        audit_chain = ApprovalChain(tenant_id=tenant.id, name="High value with audit",
                                    min_amount=Decimal("1000000"), max_amount=None,
                                    currency=tenant.currency, is_active=True)
        session.add(audit_chain)
        await session.flush()
        session.add_all([
            ApprovalStep(tenant_id=tenant.id, chain_id=audit_chain.id, step_no=1,
                         role_code="procurement_manager", threshold_amount=Decimal("0"), sla_hours=48),
            ApprovalStep(tenant_id=tenant.id, chain_id=audit_chain.id, step_no=2,
                         role_code="finance_approver", threshold_amount=Decimal("250000"), sla_hours=48),
            ApprovalStep(tenant_id=tenant.id, chain_id=audit_chain.id, step_no=3,
                         role_code="auditor", threshold_amount=Decimal("1000000"), sla_hours=48),
        ])
        created["chains"] += 1

    for role_code, amount in [("procurement_manager", "250000"), ("finance_approver", "5000000"),
                              ("controller", "5000000"), ("auditor", "10000000"),
                              ("admin", "10000000")]:
        exists = (await session.execute(
            select(ApprovalLimit).where(ApprovalLimit.tenant_id == tenant.id,
                                        ApprovalLimit.role_code == role_code,
                                        ApprovalLimit.category_code.is_(None))
        )).scalar_one_or_none()
        if not exists:
            session.add(ApprovalLimit(tenant_id=tenant.id, role_code=role_code, category_code=None,
                                      max_amount=Decimal(amount), currency=tenant.currency))
            created["limits"] += 1

    created["accounts"] = 0
    created["cost_centers"] = 0
    # Accounting is optional per deployment. With `ACCOUNTING_ENABLED=false` nothing here runs: a
    # pilot tenant gets no chart of accounts and no cost centres, so there is no half-configured
    # ledger to stumble into. The code and the tables are kept for when the module is switched on.
    if not settings.accounting_enabled:
        created["accounting"] = False
        return await _seed_remaining(session, tenant, created)

    created["accounting"] = True
    from app.services.accounting import ACCOUNTS, UNALLOCATED_CENTER, ensure_account, ensure_cost_center

    for code, name, name_ar, kind, normal, control in ACCOUNTS:
        existing = (await session.execute(
            select(GLAccount).where(GLAccount.tenant_id == tenant.id, GLAccount.code == code)
        )).scalar_one_or_none()
        if not existing:
            await ensure_account(session, tenant, code=code, name=name, name_ar=name_ar, kind=kind,
                                 normal_balance=normal, is_control=control)
            created["accounts"] += 1
    centre = (await session.execute(
        select(CostCenter).where(CostCenter.tenant_id == tenant.id, CostCenter.code == UNALLOCATED_CENTER)
    )).scalar_one_or_none()
    if not centre:
        await ensure_cost_center(session, tenant, code=UNALLOCATED_CENTER, name="Unallocated",
                                 name_ar="غير موزّع",
                                 description="Default dimension until a real cost centre is chosen.")
        created["cost_centers"] += 1

    return await _seed_remaining(session, tenant, created)


async def _seed_remaining(session: AsyncSession, tenant: Tenant, created: dict) -> dict:
    """Everything a tenant needs regardless of the accounting switch; ends by flushing."""
    sub = (await session.execute(select(Subscription).where(Subscription.tenant_id == tenant.id))).scalars().first()
    if not sub:
        session.add(Subscription(tenant_id=tenant.id, plan_code=tenant.plan_code, status="active", seats=10,
                                 period_start=utcnow(), period_end=utcnow().replace(year=utcnow().year + 1)))
        created["subscription"] = True

    await session.flush()
    return created


async def ensure_tenant(session: AsyncSession, *, name: str, slug: str, kind: str = "customer",
                        plan_code: str = "growth", locale: str = "ar") -> Tenant:
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant:
        return tenant
    tenant = Tenant(name=name, slug=slug, kind=kind, plan_code=plan_code, locale=locale,
                    timezone=settings.default_timezone, currency="EGP",
                    settings={"country": "EG", "discovery_review_gate": True, "currency": "EGP"})
    session.add(tenant)
    await session.flush()
    await seed_tenant_defaults(session, tenant)
    return tenant


async def ensure_user(session: AsyncSession, *, tenant: Tenant, email: str, password: str, full_name: str,
                      full_name_ar: str, roles: list[str], is_superadmin: bool = False,
                      must_change_password: bool = True) -> tuple[User, bool]:
    email = email.lower().strip()
    existing = (await session.execute(
        select(User).where(User.tenant_id == tenant.id, User.email == email)
    )).scalar_one_or_none()
    if existing:
        if not existing.hashed_password and password:
            existing.hashed_password = hash_password(password)
        return existing, False
    user = User(
        tenant_id=tenant.id, email=email, hashed_password=hash_password(password),
        full_name=full_name, full_name_ar=full_name_ar, roles=roles, primary_role=roles[0] if roles else "requester",
        is_superadmin=is_superadmin, must_change_password=must_change_password, locale=tenant.locale,
        job_title=("Platform Owner" if is_superadmin else "Procurement Administrator"),
    )
    session.add(user)
    await session.flush()
    return user, True


def _write_dev_credentials(entries: list[tuple[str, str, str]]) -> None:
    path = REPO_DIR / ".dev-credentials"
    lines = ["# ProcureAI development credentials (generated once) — DO NOT COMMIT", ""]
    for email, password, role in entries:
        lines.append(f"{role}: {email} / {password}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _validate_prod_password(password: str, email: str) -> None:
    errors = password_policy_errors(password, email)
    if errors:
        raise RuntimeError(
            "Refusing to bootstrap in production: BOOTSTRAP password does not satisfy policy: "
            + "; ".join(errors)
        )


async def bootstrap(session: AsyncSession, *, force: bool = False, demo: bool | None = None) -> dict:
    """Idempotent bootstrap: platform tenant + SuperAdmin, optional demo tenant + Admin."""
    if not force and settings.is_prod and not settings.bootstrap_on_start:
        return {"skipped": True, "reason": "BOOTSTRAP_ON_START=false in prod"}
    demo = settings.bootstrap_demo_tenant if demo is None else demo
    report: dict = {"created": [], "tenants": [], "generated_passwords": {}}

    await seed_reference_data(session)
    platform = await ensure_tenant(session, name="Mantiq Technologies", slug="mantiq",
                                   kind="platform", plan_code="enterprise", locale="en")
    report["tenants"].append({"slug": platform.slug, "name": platform.name, "kind": platform.kind})

    sa_email = settings.bootstrap_superadmin_email
    sa_password = settings.bootstrap_superadmin_password
    if not sa_password:
        if settings.is_prod:
            raise RuntimeError("BOOTSTRAP_SUPERADMIN_PASSWORD is required in production.")
        sa_password = random_password()
        report["generated_passwords"]["superadmin"] = sa_password
    sa_user, sa_created = await ensure_user(
        session, tenant=platform, email=sa_email, password=sa_password, full_name="Platform Owner",
        full_name_ar="مالك المنصة", roles=["superadmin"], is_superadmin=True,
    )
    if sa_created:
        report["created"].append({"email": sa_user.email, "role": "superadmin", "tenant": platform.slug})

    if demo:
        tenant = await ensure_tenant(session, name="Nile Manufacturing Group", slug="nile",
                                     kind="customer", plan_code="growth", locale="ar")
        report["tenants"].append({"slug": tenant.slug, "name": tenant.name, "kind": tenant.kind})
        admin_password = settings.bootstrap_admin_password or settings.bootstrap_demo_password
        if not admin_password:
            admin_password = random_password()
            report["generated_passwords"]["admin"] = admin_password
        if settings.is_prod and not settings.bootstrap_admin_password:
            raise RuntimeError("BOOTSTRAP_ADMIN_PASSWORD is required in production when demo tenant is enabled.")
        admin_user, admin_created = await ensure_user(
            session, tenant=tenant, email=settings.bootstrap_admin_email, password=admin_password,
            full_name="Nour El-Sayed", full_name_ar="نور السيد", roles=["admin"],
        )
        if admin_created:
            report["created"].append({"email": admin_user.email, "role": "admin", "tenant": tenant.slug})

    await audit(session, tenant_id=platform.id, action="system.bootstrap", actor_type="system",
                actor_label="system:bootstrap", entity_type="tenant", entity_id=platform.id,
                after={"tenants": report["tenants"], "created": report["created"]})
    await session.commit()

    if report["generated_passwords"]:
        banner = [
            "=" * 72,
            "  PROCUREAI FIRST-RUN CREDENTIALS (shown once — change on first login)",
            f"  SuperAdmin: {sa_email} / {report['generated_passwords'].get('superadmin', '(from env)')}",
        ]
        if demo:
            banner.append(
                f"  Admin:      {settings.bootstrap_admin_email} / "
                f"{report['generated_passwords'].get('admin', settings.bootstrap_demo_password)}"
            )
        banner += ["=" * 72]
        log.warning("\n".join(banner))
        _write_dev_credentials([
            (sa_email, report["generated_passwords"].get("superadmin", "(from env)"), "SuperAdmin"),
            (settings.bootstrap_admin_email,
             report["generated_passwords"].get("admin", settings.bootstrap_demo_password), "Admin"),
        ])
    log.info("bootstrap complete: %s", report)
    return report
