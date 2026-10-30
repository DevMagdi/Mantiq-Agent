"""Orchestration graph: a resumable state machine with human-interrupt nodes.

Stages: intake -> clarify -> normalize -> supplier_select -> rfq_generate -> dispatch ->
await_replies -> parse -> verify(HITL) -> negotiate(loop) -> compare -> recommend -> approval.
Every executed stage is checkpointed in `agent_run`/`agent_step`, so a crashed worker can
resume from the last completed node, and the audit UI can replay the reasoning.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entities import (
    RFQ,
    AgentRun,
    AgentStep,
    NegotiationPolicy,
    Offer,
    PRLine,
    PurchaseRequisition,
    RFQLine,
    RFQSupplier,
    Supplier,
    Tenant,
)
from app.services import numbers

log = logging.getLogger("procureai.graph")

STAGES = [
    "intake", "clarify", "normalize", "supplier_select", "rfq_generate", "dispatch",
    "await_replies", "parse", "verify", "negotiate", "compare", "recommend", "approval",
]
HUMAN_INTERRUPTS = {"await_replies", "verify", "approval"}
TERMINAL = {"approval"}


@dataclass
class GraphState:
    tenant_id: str
    pr_id: str | None = None
    rfq_id: str | None = None
    stage: str = "intake"
    completed: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    waiting_for_human: bool = False
    blocked_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class AgentGraph:
    """Runs stage handlers in order, stopping at human-interrupt nodes."""

    def __init__(self, handler_overrides: dict[str, Any] | None = None) -> None:
        self.handlers = {
            "intake": self._intake,
            "clarify": self._clarify,
            "normalize": self._normalize,
            "supplier_select": self._supplier_select,
            "rfq_generate": self._rfq_generate,
            "dispatch": self._dispatch,
            "await_replies": self._await_replies,
            "parse": self._parse,
            "verify": self._verify,
            "negotiate": self._negotiate,
            "compare": self._compare,
            "recommend": self._recommend,
            "approval": self._approval,
        }
        self.handlers.update(handler_overrides or {})

    async def run(self, session: AsyncSession, tenant: Tenant, state: GraphState, *,
                  actor_id: str | None = None, auto_dispatch: bool = False,
                  stop_before: str | None = None) -> GraphState:
        run = AgentRun(
            tenant_id=tenant.id, kind="orchestration", status="running", ref_type="rfq",
            ref_id=state.rfq_id, model="graph:v1", created_by=actor_id,
            input_summary=f"start at {state.stage}", started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        state.data["run_id"] = run.id
        step_no = 0
        index = STAGES.index(state.stage) if state.stage in STAGES else 0
        for stage in STAGES[index:]:
            if stop_before and stage == stop_before:
                state.blocked_reason = f"stopped before {stage}"
                break
            handler = self.handlers.get(stage)
            if handler is None:
                continue
            started = datetime.now(UTC)
            try:
                outcome = await handler(session, tenant, state, actor_id, auto_dispatch)
            except Exception as exc:  # a stage failure checkpoints and surfaces, never crashes the API
                log.warning("stage %s failed: %s", stage, exc)
                outcome = {"status": "error", "error": str(exc)[:400]}
            step_no += 1
            session.add(AgentStep(
                tenant_id=tenant.id, run_id=run.id, step_no=step_no, node=stage,
                status=outcome.get("status", "ok"), input={"state": state.stage},
                output={k: v for k, v in outcome.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))},
                reasoning=str(outcome.get("reasoning", ""))[:4000],
                latency_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
            ))
            state.completed.append(stage)
            state.stage = stage
            state.data.update({k: v for k, v in outcome.items() if k not in {"status", "reasoning"}})
            if outcome.get("status") == "error":
                state.blocked_reason = outcome.get("error", "stage error")
                run.status = "failed"
                break
            if stage in HUMAN_INTERRUPTS:
                state.waiting_for_human = True
                run.status = "paused"
                break
            if stage in TERMINAL:
                run.status = "completed"
                break
        else:
            run.status = "completed"

        if run.status == "running":
            run.status = "completed"
        run.output_summary = f"completed: {', '.join(state.completed)}"
        run.reasoning = state.blocked_reason[:2000]
        run.finished_at = datetime.now(UTC)
        run.ref_id = state.rfq_id or state.pr_id
        await session.flush()
        return state

    # ------------------------------------------------------------- stages
    async def _intake(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        pr = await _load_pr(session, state)
        if pr is None:
            return {"status": "error", "error": "No purchase requisition attached"}
        lines = list((await session.execute(select(PRLine).where(PRLine.pr_id == pr.id))).scalars().all())
        if not lines:
            return {"status": "error", "error": "Requisition has no lines"}
        state.pr_id = pr.id
        return {"status": "ok", "pr_code": pr.code, "lines": len(lines),
                "reasoning": f"Requisition {pr.code} validated with {len(lines)} line(s)."}

    async def _clarify(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        from app.services.intake import ask_clarifications

        pr = await _load_pr(session, state)
        questions = await ask_clarifications(session, tenant, pr)
        open_questions = [q for q in questions if q.status == "open"]
        return {"status": "ok", "clarifications": len(open_questions),
                "resolved": pr.clarification_resolved,
                "reasoning": f"Asked {len(open_questions)} clarifying question(s); "
                             f"{'all resolved' if pr.clarification_resolved else 'awaiting buyer answers'}."}

    async def _normalize(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        from app.services.catalog_data import TAXONOMY
        from app.services.intake import confirm_line_normalization
        from app.services.taxonomy import Classifier

        lines = list((await session.execute(select(PRLine).where(PRLine.pr_id == state.pr_id))).scalars().all())
        normalized = 0
        for line in lines:
            if line.taxonomy_code:
                continue
            classifier = Classifier(TAXONOMY)
            (code, confidence), _ = classifier.best(line.description)
            if code and confidence >= 0.8:
                await confirm_line_normalization(session, tenant, line, code)
                normalized += 1
        return {"status": "ok", "auto_normalized": normalized,
                "reasoning": f"Auto-confirmed {normalized} high-confidence taxonomy match(es)."}

    async def _supplier_select(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        rfq = await _load_rfq(session, state)
        if rfq is None:
            return {"status": "ok", "invited": 0, "reasoning": "No RFQ yet: supplier selection deferred."}
        invites = list((await session.execute(
            select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id)
        )).scalars().all())
        return {"status": "ok", "invited": len(invites),
                "reasoning": f"{len(invites)} supplier(s) invited on RFQ {rfq.code}."}

    async def _rfq_generate(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        rfq = await _load_rfq(session, state)
        if rfq:
            return {"status": "ok", "rfq_id": rfq.id, "created": False}
        pr = await _load_pr(session, state)
        suppliers = list((await session.execute(
            select(Supplier).where(Supplier.tenant_id == tenant.id, Supplier.status.in_(["approved", "preferred"]))
            .order_by(Supplier.overall_score.desc()).limit(5)
        )).scalars().all())
        if not suppliers:
            return {"status": "error", "error": "No approved suppliers available for this tenant."}
        from app.services.rfq import create_rfq_from_pr

        rfq = await create_rfq_from_pr(session, tenant, pr, supplier_ids=[s.id for s in suppliers],
                                       buyer_id=actor_id)
        state.rfq_id = rfq.id
        return {"status": "ok", "rfq_id": rfq.id, "created": True, "code": rfq.code,
                "reasoning": f"Created RFQ {rfq.code} with {len(suppliers)} approved suppliers."}

    async def _dispatch(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        rfq = await _load_rfq(session, state)
        if rfq is None or not auto_dispatch:
            return {"status": "ok", "sent": 0, "deferred": True,
                    "reasoning": "Dispatch requires an explicit human send action."}
        from app.services.rfq import dispatch_rfq

        result = await dispatch_rfq(session, tenant, rfq, actor_id=actor_id)
        return {"status": "ok", "sent": result["sent"], "results": result["results"]}

    async def _await_replies(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        rfq = await _load_rfq(session, state)
        if rfq is None:
            return {"status": "error", "error": "No RFQ to await replies for"}
        invitations = list((await session.execute(
            select(RFQSupplier).where(RFQSupplier.rfq_id == rfq.id)
        )).scalars().all())
        pending = [i for i in invitations if i.status not in {"responded", "declined"}]
        return {"status": "paused" if pending else "ok", "pending": len(pending),
                "reasoning": f"Waiting on {len(pending)} supplier(s); replies are parsed on arrival."}

    async def _parse(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        from app.db.models.entities import ExtractionResult

        rfq = await _load_rfq(session, state)
        if rfq is None:
            return {"status": "ok", "extracted": 0}
        rows = list((await session.execute(
            select(ExtractionResult).where(ExtractionResult.rfq_id == rfq.id)
        )).scalars().all())
        return {"status": "ok", "extracted": len(rows),
                "needs_review": sum(1 for r in rows if r.needs_review)}

    async def _verify(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        from app.db.models.entities import ExtractionResult

        rfq = await _load_rfq(session, state)
        if rfq is None:
            return {"status": "ok", "pending_verification": 0}
        pending = list((await session.execute(
            select(ExtractionResult).where(ExtractionResult.rfq_id == rfq.id,
                                           ExtractionResult.status.in_(["needs_review", "parsed"]))
        )).scalars().all())
        return {"status": "paused" if pending else "ok", "pending_verification": len(pending),
                "reasoning": f"{len(pending)} extraction(s) require buyer verification before comparison."}

    async def _negotiate(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        rfq = await _load_rfq(session, state)
        if rfq is None:
            return {"status": "ok", "drafted": 0}
        policy = (await session.execute(
            select(NegotiationPolicy).where(NegotiationPolicy.tenant_id == tenant.id,
                                            NegotiationPolicy.rfq_id == rfq.id,
                                            NegotiationPolicy.is_active.is_(True))
        )).scalars().first()
        if not policy:
            return {"status": "ok", "drafted": 0, "reasoning": "No negotiation policy; skipped."}
        offers = list((await session.execute(
            select(Offer).where(Offer.rfq_id == rfq.id, Offer.status != "rejected")
        )).scalars().all())
        target = float(policy.target_price or 0)
        candidates = [o for o in offers if float(o.total_base or 0) > target > 0]
        drafted = 0
        from app.services.negotiation import draft_counter_offer

        for offer in candidates[:3]:
            supplier = (await session.execute(select(Supplier).where(Supplier.id == offer.supplier_id))).scalar_one()
            result = await draft_counter_offer(session, tenant, rfq=rfq, supplier=supplier, offer=offer,
                                               policy=policy, round_no=1, actor_id=actor_id,
                                               send_requested=policy.mode == "auto")
            if result.get("status") in {"draft", "sent"}:
                drafted += 1
        return {"status": "ok", "drafted": drafted,
                "reasoning": f"Drafted {drafted} counter-offer(s); mode={policy.mode}."}

    async def _compare(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        rfq = await _load_rfq(session, state)
        if rfq is None:
            return {"status": "ok", "offers": 0}
        from app.services.comparison import build_matrix

        matrix = await build_matrix(session, tenant, rfq)
        state.data["matrix_generated"] = True
        return {"status": "ok", "offers": len(matrix["columns"]),
                "blocked_for_low_confidence": matrix["blocked_for_low_confidence"],
                "reasoning": f"Scored {len(matrix['columns'])} offer(s) with TCO and weighted criteria."}

    async def _recommend(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        rfq = await _load_rfq(session, state)
        if rfq is None:
            return {"status": "ok", "recommended": 0}
        from app.services.comparison import recommend

        result = await recommend(session, tenant, rfq, actor_id=actor_id)
        state.data["recommendation"] = result.get("recommended", [])[:3]
        return {"status": "ok", "recommended": len(result.get("recommended", [])),
                "reasoning": result.get("justification_en", "")[:1000]}

    async def _approval(self, session, tenant, state, actor_id, auto_dispatch) -> dict:
        from app.services.approval import request_approval

        rfq = await _load_rfq(session, state)
        if rfq is None:
            return {"status": "ok", "approvals": 0}
        offers = list((await session.execute(
            select(Offer).where(Offer.rfq_id == rfq.id, Offer.status != "rejected")
            .order_by(Offer.weighted_score.desc())
        )).scalars().all())
        if not offers:
            return {"status": "ok", "approvals": 0}
        best = offers[0]
        approvals = await request_approval(session, tenant, rfq, offer_ids=[best.id],
                                           amount=numbers.money(best.tco_total or 0), requested_by=actor_id)
        return {"status": "paused", "approvals": len(approvals),
                "reasoning": "Human approval is mandatory before any purchase order."}


async def _load_pr(session: AsyncSession, state: GraphState) -> PurchaseRequisition | None:
    if state.pr_id:
        return (await session.execute(
            select(PurchaseRequisition).where(PurchaseRequisition.id == state.pr_id)
        )).scalar_one_or_none()
    rfq = await _load_rfq(session, state)
    if rfq and rfq.pr_id:
        return (await session.execute(
            select(PurchaseRequisition).where(PurchaseRequisition.id == rfq.pr_id)
        )).scalar_one_or_none()
    return None


async def _load_rfq(session: AsyncSession, state: GraphState) -> RFQ | None:
    if not state.rfq_id:
        return None
    return (await session.execute(select(RFQ).where(RFQ.id == state.rfq_id))).scalar_one_or_none()


async def rfq_lines(session: AsyncSession, rfq_id: str) -> list[RFQLine]:
    return list((await session.execute(
        select(RFQLine).where(RFQLine.rfq_id == rfq_id).order_by(RFQLine.line_no)
    )).scalars().all())


graph = AgentGraph()
