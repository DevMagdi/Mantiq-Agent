"""Approval-chain service internals and authentication hardening (FR-CMP-3..5, NFR-SEC-*).

These are the two places where a mistake is expensive: an approval that advances too early
lets an unauthorised award through, and a weak session lets anyone claim to be an approver.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import Forbidden, Unauthorized, ValidationFailed
from app.db.models.entities import (
    RFQ,
    ApprovalChain,
    ApprovalLimit,
    Offer,
    PurchaseOrder,
    Tenant,
    User,
)
from app.db.session import SessionLocal
from app.services import approval as approval_service
from app.services import auth

DEMO_PASSWORD = "Demo!Pilot2026"


async def _tenant(session) -> Tenant:
    return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


async def _user(session, email: str) -> User:
    return (await session.execute(select(User).where(User.email == email))).scalar_one()


async def _fresh_rfq(session, tenant) -> tuple[RFQ, Offer]:
    """An RFQ whose offers are still open, so awarding it is observable."""
    rfq = (await session.execute(
        select(RFQ).where(RFQ.tenant_id == tenant.id).order_by(RFQ.created_at)
    )).scalars().first()
    offer = (await session.execute(
        select(Offer).where(Offer.rfq_id == rfq.id).order_by(Offer.total_base.asc())
    )).scalars().first()
    rfq.status = "open"
    rfq.stage_history = [*(rfq.stage_history or []), {"stage": "open", "actor": "pytest"}]
    for other in (await session.execute(select(Offer).where(Offer.rfq_id == rfq.id))).scalars().all():
        other.status = "verified"
    await session.flush()
    return rfq, offer


# ------------------------------------------------------------------ approvals
async def test_chain_falls_back_to_approval_limits_when_no_chain_matches():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq, _offer = await _fresh_rfq(session, tenant)
        from decimal import Decimal
        for row in (await session.execute(
            select(ApprovalChain).where(ApprovalChain.tenant_id == tenant.id))).scalars().all():
            row.is_active = False
        await session.flush()

        limits = list((await session.execute(
            select(ApprovalLimit).where(ApprovalLimit.tenant_id == tenant.id)
        )).scalars().all())
        assert limits, "the demo tenant must have approval limits configured"
        ceiling = max(Decimal(str(limit.max_amount or 0)) for limit in limits)

        in_band, steps = await approval_service.resolve_chain(session, tenant, rfq, ceiling)
        assert in_band is None, "the fallback deliberately returns no chain row"
        assert len(steps) == 1
        assert steps[0].role_code in {limit.role_code for limit in limits}

        above, above_steps = await approval_service.resolve_chain(
            session, tenant, rfq, ceiling + Decimal("1"))
        assert above is None
        assert above_steps[0].role_code == "finance_approver", \
            "an amount above every limit escalates to finance"
        await session.rollback()


async def test_request_approval_is_idempotent_while_a_request_is_open():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq, offer = await _fresh_rfq(session, tenant)
        first = await approval_service.request_approval(
            session, tenant, rfq, offer_ids=[offer.id], amount=offer.total_base or 0)
        second = await approval_service.request_approval(
            session, tenant, rfq, offer_ids=[offer.id], amount=offer.total_base or 0)
        assert {row.id for row in second} <= {row.id for row in first}
        assert len(second) == 1, "only the actionable step is returned on a repeat request"
        assert second[0].status == "pending"
        assert first[0].status == "pending"
        assert all(row.due_at for row in first)
        await session.rollback()


async def test_chain_advances_step_by_step_then_awards_and_issues_a_purchase_order():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq, offer = await _fresh_rfq(session, tenant)
        rows = await approval_service.request_approval(
            session, tenant, rfq, offer_ids=[offer.id], amount=offer.total_base or 0)
        assert len(rows) >= 2, "the demo tenant's chain has multiple levels"

        manager = await _user(session, "manager@nile.example.com")
        finance = await _user(session, "finance@nile.example.com")
        auditor = await _user(session, "auditor@nile.example.com")

        first = await approval_service.decide(session, tenant, rows[0], decision="approve",
                                             comment="Within budget", user=manager)
        assert first["ok"] is True
        assert first["status"] == "approved"
        assert first["next_step"] == rows[1].step_no
        assert rows[1].status == "pending"
        assert not first.get("purchase_orders"), "no PO before the final level"

        # Above the high-value band the auditor joins the chain: every step is decided in order.
        approvers = {"procurement_manager": manager, "finance_approver": finance, "auditor": auditor}
        for row in rows[1:-1]:
            row.status = "pending"
            step = await approval_service.decide(session, tenant, row, decision="approve",
                                                 comment="Checked", user=approvers[row.approver_role])
            assert step["ok"] is True and not step.get("purchase_orders")
        rows[-1].status = "pending"
        last = await approval_service.decide(session, tenant, rows[-1], decision="approve",
                                             comment="Cash confirmed", user=approvers[rows[-1].approver_role])
        assert last["ok"] is True
        assert last["next_step"] is None
        assert last["purchase_orders"], "final approval must issue the purchase order"
        po = (await session.execute(select(PurchaseOrder).where(PurchaseOrder.id == last["purchase_orders"][0]))).scalar_one()
        assert po.rfq_id == rfq.id
        assert rfq.status == "closed"
        await session.rollback()


async def test_a_split_award_issues_one_purchase_order_per_supplier():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq, offer = await _fresh_rfq(session, tenant)
        offers = list((await session.execute(select(Offer).where(Offer.rfq_id == rfq.id))).scalars().all())
        split = [{"offer_id": row.id, "share": 0.6} for row in offers[:2]]
        rows = await approval_service.request_approval(
            session, tenant, rfq, offer_ids=[offer.id], amount=offer.total_base or 0, split=split)
        for row in rows[:-1]:
            await approval_service.decide(session, tenant, row, decision="approve", comment="ok",
                                          user=await _user(session, "manager@nile.example.com"))
        final = await approval_service.decide(session, tenant, rows[-1], decision="approve",
                                              comment="split approved",
                                              user=await _user(session, "finance@nile.example.com"))
        assert len(final["purchase_orders"]) == 2
        assert all(row.status == "selected" for row in offers[:2])
        await session.rollback()


async def test_deciding_an_already_decided_approval_is_refused():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq, offer = await _fresh_rfq(session, tenant)
        rows = await approval_service.request_approval(
            session, tenant, rfq, offer_ids=[offer.id], amount=offer.total_base or 0)
        manager = await _user(session, "manager@nile.example.com")
        await approval_service.decide(session, tenant, rows[0], decision="approve", comment="ok", user=manager)
        again = await approval_service.decide(session, tenant, rows[0], decision="approve",
                                              comment="twice", user=manager)
        assert again["ok"] is False
        assert "not pending" in again["error"]
        await session.rollback()


async def test_overdue_approvals_are_escalated_once_and_rescheduled():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq, offer = await _fresh_rfq(session, tenant)
        rows = await approval_service.request_approval(
            session, tenant, rfq, offer_ids=[offer.id], amount=offer.total_base or 0)
        rows[0].due_at = datetime.now(UTC) - timedelta(hours=2)
        await session.flush()

        assert await approval_service.escalate_overdue(session, tenant) >= 1
        assert rows[0].due_at > datetime.now(UTC)
        assert await approval_service.escalate_overdue(session, tenant) == 0
        await session.rollback()


async def test_delegation_is_recorded_in_the_approval_trail():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq, offer = await _fresh_rfq(session, tenant)
        rows = await approval_service.request_approval(
            session, tenant, rfq, offer_ids=[offer.id], amount=offer.total_base or 0)
        manager = await _user(session, "manager@nile.example.com")
        delegate_to = await _user(session, "buyer@nile.example.com")
        await approval_service.delegate(session, tenant, rows[0], delegate_to.id, manager, comment="away")
        assert rows[0].delegated_to == delegate_to.id
        assert "delegated by" in rows[0].comment
        await session.rollback()


# ------------------------------------------------------------------ authentication
async def test_repeated_failures_lock_the_account_and_lockout_is_enforced():
    async with SessionLocal() as session:
        await _tenant(session)
        user = await _user(session, "requester@nile.example.com")
        user.failed_logins = 0
        user.locked_until = None
        await session.flush()

        for _attempt in range(auth.MAX_FAILED_LOGINS):
            with pytest.raises(Unauthorized):
                await auth.login(session, email=user.email, password="wrong-password")
        assert user.failed_logins >= auth.MAX_FAILED_LOGINS
        assert user.locked_until is not None

        with pytest.raises(Forbidden):
            await auth.login(session, email=user.email, password=DEMO_PASSWORD)
        await session.rollback()


async def test_unknown_email_and_disabled_account_are_rejected():
    async with SessionLocal() as session:
        with pytest.raises(Unauthorized):
            await auth.login(session, email="ghost@nile.example.com", password="whatever")
        user = await _user(session, "buyer@nile.example.com")
        user.is_active = False
        await session.flush()
        with pytest.raises(Forbidden):
            await auth.login(session, email=user.email, password=DEMO_PASSWORD)
        await session.rollback()


async def test_refresh_tokens_rotate_and_cannot_be_replayed():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        user = await _user(session, "buyer@nile.example.com")
        tokens = await auth.issue_tokens(session, user, tenant, user_agent="pytest", ip="127.0.0.1")
        rotated = await auth.refresh(session, tokens["refresh_token"], user_agent="pytest")
        assert rotated["access_token"] and rotated["refresh_token"]
        assert rotated["refresh_token"] != tokens["refresh_token"]
        with pytest.raises(Unauthorized):
            await auth.refresh(session, tokens["refresh_token"])

        await auth.logout(session, rotated["refresh_token"])
        with pytest.raises(Unauthorized):
            await auth.refresh(session, rotated["refresh_token"])
        await session.rollback()


async def test_sessions_can_be_listed_and_revoked():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        user = await _user(session, "buyer@nile.example.com")
        await auth.issue_tokens(session, user, tenant, user_agent="pytest", ip="10.0.0.9")
        sessions = await auth.list_sessions(session, user)
        assert sessions
        assert any(row["user_agent"] == "pytest" for row in sessions)

        await auth.revoke_session(session, user, sessions[-1]["id"])
        remaining = await auth.list_sessions(session, user)
        assert sessions[-1]["id"] not in {row["id"] for row in remaining}
        await session.rollback()


async def test_password_change_requires_the_current_password_and_policy_compliance():
    async with SessionLocal() as session:
        user = await _user(session, "buyer@nile.example.com")
        original = user.hashed_password
        with pytest.raises(Unauthorized):
            await auth.change_password(session, user, current="nope", new="Str0ng!Passphrase2026")
        with pytest.raises(ValidationFailed):
            await auth.change_password(session, user, current=DEMO_PASSWORD, new="short")
        assert user.hashed_password == original

        await auth.change_password(session, user, current=DEMO_PASSWORD, new="Str0ng!Passphrase2026")
        assert user.must_change_password is False
        assert user.hashed_password != original
        await session.rollback()


async def test_password_reset_does_not_leak_account_existence():
    async with SessionLocal() as session:
        assert await auth.request_password_reset(session, "nobody@nowhere.invalid") == {"sent": True}

        tenant = await _tenant(session)
        user = await _user(session, "requester@nile.example.com")
        assert await auth.request_password_reset(session, user.email) == {"sent": True}
        token = (tenant.settings or {}).get("password_resets", {}).get(user.id, {}).get("token")
        assert token, "a reset token must be stored, never e-mailed in plaintext elsewhere"

        result = await auth.confirm_password_reset(session, token, "An0ther!Passphrase2026")
        assert result["ok"] is True
        assert result["email"] == user.email
        with pytest.raises(ValidationFailed):
            await auth.confirm_password_reset(session, token, "An0ther!Passphrase2026")
        await session.rollback()


async def test_expired_reset_token_is_rejected():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        user = await _user(session, "requester@nile.example.com")
        await auth.request_password_reset(session, user.email)
        resets = dict((tenant.settings or {}).get("password_resets", {}))
        entry = dict(resets.get(user.id) or {})
        entry["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        resets[user.id] = entry
        tenant.settings = {**(tenant.settings or {}), "password_resets": resets}
        await session.flush()
        with pytest.raises(ValidationFailed):
            await auth.confirm_password_reset(session, entry["token"], "An0ther!Passphrase2026")
        await session.rollback()


def test_generated_passwords_satisfy_the_policy():
    from app.core.security import password_policy_errors

    for _ in range(5):
        candidate = auth.generate_password()
        assert password_policy_errors(candidate, "user@example.com") == []
