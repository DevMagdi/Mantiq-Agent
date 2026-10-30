"""Agent orchestration: fixed tool registry, audit trail, human-interrupt nodes (FR-AGT-*).

The architectural claim under test is "the LLM proposes, the policy engine disposes":
the tool surface is closed, every call is audited, forbidden verbs do not exist, and the
graph always parks at a human checkpoint before anything commercial happens.
"""
from __future__ import annotations

from sqlalchemy import select

from app.agents.graph import HUMAN_INTERRUPTS, STAGES, AgentGraph, GraphState
from app.agents.tools import FORBIDDEN_TOOL_NAMES, TOOLS, ToolContext, invoke_tool, tool_catalog
from app.db.models.entities import RFQ, AgentRun, AgentStep, Tenant, ToolCall, User
from app.db.session import SessionLocal


async def _tenant(session) -> Tenant:
    return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


async def _buyer_user(session, tenant) -> User:
    return (await session.execute(
        select(User).where(User.tenant_id == tenant.id, User.email == "buyer@nile.example.com")
    )).scalar_one()


async def _seeded_rfq(session) -> RFQ:
    """The demo tenant's first historical RFQ (closed, with verified offers)."""
    return (await session.execute(
        select(RFQ).where(RFQ.status == "closed").order_by(RFQ.created_at)
    )).scalars().first()


def test_tool_registry_is_closed_and_forbidden_verbs_absent():
    catalog = {item["name"]: item for item in tool_catalog()}
    assert catalog, "the agent must expose its tools"
    assert set(catalog) == set(TOOLS)
    assert not (FORBIDDEN_TOOL_NAMES & set(catalog)), "accept/sign/pay tools must never be registered"
    for item in catalog.values():
        assert item["permission"], "every tool declares an RBAC permission"
        assert "input_schema" in item and item["input_schema"]["type"] == "object"
    # Mutating tools must be the minority and must be marked.
    assert catalog["send_rfq"]["mutating"] is True
    assert catalog["compute_tco"]["mutating"] is False
    assert catalog["propose_counter"]["requires_policy"] is True


def test_graph_stages_declare_their_human_checkpoints():
    assert STAGES[0] == "intake" and STAGES[-1] == "approval"
    assert HUMAN_INTERRUPTS == {"await_replies", "verify", "approval"}
    assert len(STAGES) == len(set(STAGES))


async def test_invoke_tool_rejects_forbidden_unknown_and_malformed_calls():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        ctx = ToolContext(tenant_id=tenant.id, task="generic")
        for name in ("accept_offer", "sign_contract", "pay_invoice"):
            result = await invoke_tool(session, tenant, ctx, name, {})
            assert result["ok"] is False
            assert "does not exist" in result["error"]

        unknown = await invoke_tool(session, tenant, ctx, "drop_database", {})
        assert unknown["ok"] is False and "Unknown tool" in unknown["error"]

        malformed = await invoke_tool(session, tenant, ctx, "normalize_item", {"nope": 1})
        assert malformed["ok"] is False and "Invalid arguments" in malformed["error"]

        not_allowed = await invoke_tool(session, tenant, ctx, "normalize_item",
                                       {"description": "Rebar 12mm"}, allowed={"search_suppliers"})
        assert not_allowed["ok"] is False and "not allowed" in not_allowed["error"]
        await session.rollback()


async def test_invoke_tool_executes_and_audits_every_call():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        buyer = await _buyer_user(session, tenant)
        ctx = ToolContext(tenant_id=tenant.id, user_id=buyer.id, task="normalize")

        result = await invoke_tool(session, tenant, ctx, "normalize_item",
                                   {"description": "Rebar 12mm B420D deformed steel bar"})
        assert result["_ok"] is True, result
        assert result["_tool"] == "normalize_item"
        await session.flush()

        record = (await session.execute(
            select(ToolCall).where(ToolCall.tenant_id == tenant.id,
                                   ToolCall.tool_name == "normalize_item")
        )).scalars().first()
        assert record is not None
        assert record.status == "ok"
        assert record.args["description"].startswith("Rebar")
        await session.rollback()


async def test_invoke_tool_enforces_rbac_permission():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        requester = (await session.execute(
            select(User).where(User.tenant_id == tenant.id, User.email == "requester@nile.example.com")
        )).scalar_one()
        ctx = ToolContext(tenant_id=tenant.id, user_id=requester.id)
        # request_approval needs approval:read, which a requester does not hold.
        result = await invoke_tool(session, tenant, ctx, "request_approval",
                                   {"rfq_id": "x", "offer_ids": [], "amount": "1"})
        assert result["ok"] is False
        assert "Missing permission" in result["error"]
        await session.rollback()


async def test_graph_stops_at_the_first_human_interrupt():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        buyer = await _buyer_user(session, tenant)
        rfq = await _seeded_rfq(session)
        assert rfq is not None

        state = GraphState(tenant_id=tenant.id, rfq_id=rfq.id)
        graph = AgentGraph()
        result = await graph.run(session, tenant, state, actor_id=buyer.id)

        assert result.waiting_for_human is True
        assert result.stage == "await_replies"
        assert result.completed[-1] == "await_replies"
        assert "verify" not in result.completed, "the graph never skips a human checkpoint"
        assert "approval" not in result.completed

        run = (await session.execute(select(AgentRun).where(AgentRun.id == result.data["run_id"]))).scalar_one()
        assert run.status == "paused"
        assert run.finished_at is not None
        steps = list((await session.execute(
            select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.step_no)
        )).scalars().all())
        assert [step.node for step in steps] == result.completed
        assert all(step.latency_ms >= 0 for step in steps)
        await session.rollback()


async def test_graph_honours_stop_before_and_reports_a_reason():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _seeded_rfq(session)
        state = GraphState(tenant_id=tenant.id, rfq_id=rfq.id)
        result = await AgentGraph().run(session, tenant, state, stop_before="supplier_select")
        assert result.completed == ["intake", "clarify", "normalize"]
        assert "stopped before supplier_select" in result.blocked_reason
        assert result.waiting_for_human is False
        await session.rollback()


async def test_graph_records_a_failed_stage_instead_of_raising():
    async with SessionLocal() as session:
        tenant = await _tenant(session)

        async def explode(session_, tenant_, state_, actor_id_, auto_dispatch_):
            raise RuntimeError("upstream timeout")

        state = GraphState(tenant_id=tenant.id)
        result = await AgentGraph({"intake": explode}).run(session, tenant, state)

        assert result.completed == ["intake"]
        assert "upstream timeout" in result.blocked_reason
        run = (await session.execute(select(AgentRun).where(AgentRun.id == result.data["run_id"]))).scalar_one()
        assert run.status == "failed"
        step = (await session.execute(select(AgentStep).where(AgentStep.run_id == run.id))).scalar_one()
        assert step.status == "error"
        await session.rollback()


async def test_graph_without_a_requisition_reports_an_error():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        result = await AgentGraph().run(session, tenant, GraphState(tenant_id=tenant.id))
        assert "No purchase requisition attached" in result.blocked_reason
        assert result.completed == ["intake"]
        await session.rollback()


async def test_graph_defers_dispatch_without_an_explicit_human_send():
    async with SessionLocal() as session:
        tenant = await _tenant(session)
        rfq = await _seeded_rfq(session)
        state = GraphState(tenant_id=tenant.id, rfq_id=rfq.id)
        result = await AgentGraph().run(session, tenant, state, stop_before="await_replies")
        assert result.data.get("deferred") is True
        await session.rollback()


async def test_agent_endpoints_expose_the_registry_and_run_the_graph(client, buyer):
    tools = await client.get("/api/v1/agent/tools", headers=buyer["headers"])
    assert tools.status_code in {200, 403}
    if tools.status_code == 200:
        payload = tools.json()
        assert payload["items"]
        assert "accept_offer" in payload["forbidden"]
        assert "policy engine" in payload["policy"]

    rfqs = (await client.get("/api/v1/rfqs", headers=buyer["headers"],
                             params={"page_size": 50})).json()["items"]
    closed = [rfq for rfq in rfqs if rfq["status"] == "closed"]
    assert closed, "the demo dataset must contain a closed RFQ"
    run = await client.post("/api/v1/agent/run", headers=buyer["headers"],
                            json={"rfq_id": closed[0]["id"], "stop_before": "await_replies"})
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["completed"][0] == "intake"
    assert body["waiting_for_human"] is False
