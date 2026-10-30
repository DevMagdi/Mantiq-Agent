"""The internal assistant: permission-shaped answers, forbidden intents, full audit (2.2).

Each test asks the same kind of question as a different role and asserts two things at once — that
the answer fits the role, and that the number the role may not see never appears anywhere in the
payload.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.rbac import permissions_for
from app.db.models.entities import (
    RFQ,
    AssistantSession,
    AssistantTurn,
    AuditLog,
    Offer,
    PurchaseOrder,
    PurchaseRequisition,
    Tenant,
    User,
)
from app.db.session import SessionLocal
from app.services import assistant

PASSWORD_HASH = ""


async def _tenant(session) -> Tenant:
    return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


async def _user(session, email: str) -> User:
    return (await session.execute(select(User).where(User.email == email))).scalar_one()


async def _ask(session, tenant, email: str, text: str, **kwargs):
    user = await _user(session, email)
    permissions = permissions_for(user.roles or [], user.is_superadmin)
    return await assistant.ask(session, tenant, user, permissions, text=text, **kwargs)


async def test_a_requester_can_track_their_own_request_but_not_someone_elses():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        mine = (await session.execute(
            select(PurchaseRequisition).where(PurchaseRequisition.tenant_id == tenant.id,
                                             PurchaseRequisition.requested_by.isnot(None))
        )).scalars().first()
        assert mine is not None
        result = await _ask(session, tenant, "requester@nile.example.com",
                            f"What is the status of {mine.code}?")
        assert result["ok"] is True
        assert result["intent"] == "document_status"
        assert mine.code in result["answer_en"]
        assert result["citations"][0]["link"].startswith("/requests?focus=")
        assert result["can_act"] is False
        await session.rollback()


async def test_a_document_you_do_not_own_is_refused_without_leaking_it():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = (await session.execute(
            select(PurchaseOrder).where(PurchaseOrder.tenant_id == tenant.id)
        )).scalars().first()
        result = await _ask(session, tenant, "requester@nile.example.com", f"Where is {po.code}?")
        # The requester may read purchase orders in general, but this one is not theirs: the answer
        # says "cannot find" rather than confirming a document they may not see.
        assert result["blocked_code"] == "ASSISTANT_NOT_FOUND"
        assert po.code not in result["answer_en"]
        assert result["citations"] == []
        await session.rollback()


async def test_a_finance_user_gets_the_spoken_balance_summary_and_a_warehouse_user_does_not():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        finance = await _ask(session, tenant, "finance@nile.example.com", "What is the outstanding balance?")
        assert finance["intent"] == "supplier_balance"
        assert finance["blocked_code"] == ""
        assert "outstanding" in finance["answer_en"].lower() or "balance" in finance["answer_en"].lower()

        denied = await _ask(session, tenant, "storekeeper@nile.example.com",
                            "What is the outstanding balance?")
        assert denied["blocked_code"] == "ASSISTANT_PERMISSION"
        assert denied["data"] == {}
        assert "EGP" not in denied["answer_en"]
        await session.rollback()


async def test_approving_through_the_assistant_is_refused_in_both_languages():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        result = await _ask(session, tenant, "manager@nile.example.com", "اعتمد أفضل عرض")
        assert result["intent"] == "forbidden"
        assert result["blocked_code"] == "ASSISTANT_FORBIDDEN_ACTION"
        assert result["can_act"] is False
        assert "cannot do that" in result["answer_en"]
        assert "لا أستطيع" in result["answer_ar"]

        english = await _ask(session, tenant, "admin@pilot.procureai", "Approve the best offer")
        assert english["blocked_code"] == "ASSISTANT_FORBIDDEN_ACTION"
        await session.rollback()


async def test_the_assistant_can_draft_a_request_but_never_submits_it():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        user = await _user(session, "requester@nile.example.com")
        before = {row.code for row in (await session.execute(
            select(PurchaseRequisition).where(PurchaseRequisition.requested_by == user.id)
        )).scalars().all()}
        result = await _ask(session, tenant, "requester@nile.example.com",
                            "I need to buy 12 safety helmets for the warehouse")
        assert result["intent"] == "pr_create"
        fresh = [row for row in (await session.execute(
            select(PurchaseRequisition).where(PurchaseRequisition.requested_by == user.id)
        )).scalars().all() if row.code not in before]
        assert len(fresh) == 1, "exactly one request was drafted"
        assert fresh[0].status in {"draft", "clarifying"}
        assert fresh[0].status not in {"submitted", "approved", "rfq_created"}, (
            "the assistant must not submit on the user's behalf")
        assert result["can_act"] is False
        await session.rollback()


async def test_supplier_communications_answers_come_from_the_inbox_and_are_cited():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        result = await _ask(session, tenant, "buyer@nile.example.com",
                            "Show me the WhatsApp conversations with suppliers")
        assert result["intent"] == "supplier_communications"
        assert result["blocked_code"] == ""
        assert "thread_id" in result["data"] or result["data"].get("items") == []
        await session.rollback()


async def test_a_conversation_can_be_continued_and_every_turn_is_audited():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        first = await _ask(session, tenant, "manager@nile.example.com", "What can you do?")
        second = await _ask(session, tenant, "manager@nile.example.com", "Show the offers",
                            session_id=first["session_id"])
        assert second["session_id"] == first["session_id"]
        turns = list((await session.execute(
            select(AssistantTurn).where(AssistantTurn.session_id == first["session_id"])
            .order_by(AssistantTurn.seq)
        )).scalars().all())
        assert [turn.role for turn in turns] == ["user", "assistant", "user", "assistant"]
        view = await assistant.session_view(
            session, tenant.id,
            (await session.execute(
                select(AssistantSession).where(AssistantSession.id == first["session_id"])
            )).scalar_one(),
        )
        assert view["turns"][0]["text"]
        assert view["permission_snapshot"]

        logs = list((await session.execute(
            select(AuditLog).where(AuditLog.action == "assistant.asked",
                                   AuditLog.entity_id == first["session_id"])
        )).scalars().all())
        assert len(logs) == 2
        assert logs[0].after["intent"]
        await session.rollback()


async def test_a_disabled_assistant_flag_answers_nothing():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        tenant.settings = {**(tenant.settings or {}), "features": {"voice_assistant": False}}
        result = await _ask(session, tenant, "admin@pilot.procureai", "What is the status of PO-2026-00001?")
        assert result["ok"] is False and result["codes"] == ["BLOCK_FEATURE_OFF"]
        tenant.settings = {**(tenant.settings or {}), "features": {"voice_assistant": True}}
        await session.rollback()


async def test_recommendation_needs_the_comparison_permission():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        denied = await _ask(session, tenant, "storekeeper@nile.example.com", "Which supplier do you recommend?")
        assert denied["blocked_code"] == "ASSISTANT_PERMISSION"

        # Pick the RFQ that actually has offers so the comparison has something to rank.
        rfq_with_offers = (await session.execute(
            select(RFQ).join(Offer, Offer.rfq_id == RFQ.id).where(RFQ.tenant_id == tenant.id)
        )).scalars().first()
        question = (f"Which supplier do you recommend for {rfq_with_offers.code}?"
                    if rfq_with_offers else "Which supplier do you recommend?")
        allowed = await _ask(session, tenant, "buyer@nile.example.com", question)
        assert allowed["intent"] == "recommendation"
        assert allowed["blocked_code"] == ""
        if rfq_with_offers:
            assert "human approves the award" in allowed["answer_en"]
            assert allowed["can_act"] is False
        await session.rollback()


async def test_the_assistant_never_reports_an_award_or_acceptance_capability():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        result = await _ask(session, tenant, "admin@pilot.procureai", "What can you do?")
        assert result["can_act"] is False
        assert "award" in result["answer_en"].lower()
        assert "approve" in result["answer_en"].lower()
        assert (await session.execute(
            select(RFQ).where(RFQ.tenant_id == tenant.id)
        )).scalars().first() is not None


async def test_a_manager_can_track_a_purchase_order_through_the_lifecycle():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        po = (await session.execute(
            select(PurchaseOrder).where(PurchaseOrder.tenant_id == tenant.id)
        )).scalars().first()
        result = await _ask(session, tenant, "manager@nile.example.com", f"Where is {po.code}?")
        assert result["intent"] == "document_status"
        assert result["blocked_code"] == ""
        assert po.code in result["answer_en"]
        assert result["data"]["stage"]
        assert result["data"]["next_action"]
        await session.rollback()
