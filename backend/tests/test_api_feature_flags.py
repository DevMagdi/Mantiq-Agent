"""Feature flags are a real off switch, verified through the public API.

An admin turns a capability off in the UI; the next WhatsApp send, AI call or assistant question must
be refused by the server with a policy code — never allowed "because the button was disabled". These
tests change the flag through `PATCH /settings/features` (the same endpoint the screen uses) and then
call the real endpoints, restoring the flag afterwards so the shared tenant is left as found.
"""
from __future__ import annotations

import pytest

FEATURE_KEYS = ("whatsapp", "whatsapp_rich_messages", "voice_ai_calls", "voice_assistant")


async def _set_flags(client, headers, **values):
    response = await client.patch("/api/v1/settings/features", headers=headers,
                                  json={"features": values})
    assert response.status_code == 200, response.text
    return response.json()


async def _open_rfq(client, buyer, requester):
    pr = await client.post("/api/v1/intake/text", headers=requester["headers"], json={
        "text": "Need 25 t cement bags, target 1200 EGP per ton.", "title": "Feature-flag fixture",
    })
    pr_id = pr.json()["id"]
    detail = (await client.get(f"/api/v1/requests/{pr_id}", headers=requester["headers"])).json()
    for question in detail.get("clarifications", []):
        if question["status"] == "open":
            await client.post(f"/api/v1/requests/{pr_id}/clarifications/{question['id']}/answer",
                              headers=requester["headers"], json={"answer": "OPC 42.5"})
    await client.post(f"/api/v1/requests/{pr_id}/submit", headers=requester["headers"])
    suppliers = (await client.get("/api/v1/suppliers", headers=buyer["headers"],
                                  params={"page_size": 1})).json()["items"]
    rfq = await client.post("/api/v1/rfqs", headers=buyer["headers"], json={
        "pr_id": pr_id, "supplier_ids": [suppliers[0]["id"]], "deadline_days": 7,
    })
    rfq_id = rfq.json()["id"]
    await client.post(f"/api/v1/rfqs/{rfq_id}/dispatch", headers=buyer["headers"], json={})
    detail = (await client.get(f"/api/v1/rfqs/{rfq_id}", headers=buyer["headers"])).json()
    return rfq_id, detail["suppliers"][0]["supplier_id"]


@pytest.mark.parametrize("flag", FEATURE_KEYS)
async def test_every_documented_flag_can_be_read_and_written(client, admin, flag):
    before = (await client.get("/api/v1/settings/features", headers=admin["headers"])).json()
    current = {row["key"]: row for row in before["features"]}[flag]
    try:
        after = await _set_flags(client, admin["headers"], **{flag: False})
        state = {row["key"]: row for row in after["features"]}[flag]
        assert state["enabled"] is False and state["tenant_value"] is False
        assert state["global_allowed"] is True
        assert state["reason"] == "disabled_by_tenant"
        # …and the change survives the request: the next read sees it too.
        reread = (await client.get("/api/v1/settings/features", headers=admin["headers"])).json()
        assert {row["key"]: row for row in reread["features"]}[flag]["enabled"] is False
    finally:
        await _set_flags(client, admin["headers"], **{flag: current["tenant_value"]})


async def test_an_unknown_flag_is_rejected_instead_of_stored(client, admin):
    response = await client.patch("/api/v1/settings/features", headers=admin["headers"],
                                  json={"features": {"telepathy": True}})
    assert response.status_code == 422
    assert "telepathy" in response.text
    after = (await client.get("/api/v1/settings/features", headers=admin["headers"])).json()
    assert "telepathy" not in {row["key"] for row in after["features"]}


async def test_a_non_boolean_flag_is_rejected(client, admin):
    response = await client.patch("/api/v1/settings/features", headers=admin["headers"],
                                  json={"features": {"whatsapp": "maybe"}})
    assert response.status_code == 422


async def test_a_disabled_whatsapp_flag_stops_the_server_sending(client, buyer, requester, admin):
    rfq_id, supplier_id = await _open_rfq(client, buyer, requester)
    try:
        await _set_flags(client, admin["headers"], whatsapp=False)
        response = await client.post(f"/api/v1/rfqs/{rfq_id}/whatsapp", headers=buyer["headers"],
                                     json={"supplier_id": supplier_id, "kind": "quote_request"})
        body = response.json()
        assert body["ok"] is False
        assert "BLOCK_FEATURE_OFF" in body["codes"]
    finally:
        await _set_flags(client, admin["headers"], whatsapp=True)


async def test_a_disabled_voice_flag_stops_an_ai_call(client, buyer, requester, admin):
    rfq_id, supplier_id = await _open_rfq(client, buyer, requester)
    try:
        await _set_flags(client, admin["headers"], voice_ai_calls=False)
        response = await client.post("/api/v1/voice/calls", headers=buyer["headers"], json={
            "rfq_id": rfq_id, "supplier_id": supplier_id, "language": "ar-EG", "mode": "ai",
        })
        body = response.json()
        assert body["ok"] is False
        assert "BLOCK_FEATURE_OFF" in body["codes"]
    finally:
        await _set_flags(client, admin["headers"], voice_ai_calls=True)


async def test_a_disabled_assistant_flag_stops_the_assistant(client, manager, admin):
    try:
        await _set_flags(client, admin["headers"], voice_assistant=False)
        response = await client.post("/api/v1/assistant/ask", headers=manager["headers"],
                                     json={"text": "كم عدد عروض الأسعار المفتوحة؟"})
        assert response.status_code in {200, 403}
        if response.status_code == 200:
            assert response.json()["ok"] is False
            assert "BLOCK_FEATURE_OFF" in response.json()["codes"]
    finally:
        await _set_flags(client, admin["headers"], voice_assistant=True)


async def test_the_deployment_switch_wins_over_the_tenant_flag(client, admin, monkeypatch):
    """A tenant cannot turn on what the operator disabled for the whole installation."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "whatsapp_enabled", False)
    body = (await client.get("/api/v1/settings/features", headers=admin["headers"])).json()
    row = {item["key"]: item for item in body["features"]}["whatsapp"]
    assert row["enabled"] is False
    assert row["global_allowed"] is False
    assert row["reason"] == "disabled_by_operator"
