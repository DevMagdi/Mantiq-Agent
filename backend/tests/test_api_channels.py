"""HTTP surface of phase 3: channels, WhatsApp, inbox, AI voice and the assistant.

The tests keep the same promise as the rest of the suite: every endpoint is exercised through the real
router, the real permission dependencies and the real services. The only fake is the provider.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.adapters.base import registry
from app.adapters.whatsapp import FakeWhatsAppProvider
from app.db.models.entities import RFQ, RFQSupplier, Supplier, Tenant
from app.db.session import SessionLocal
from app.services.messaging import set_opt_in
from app.services.suppliers import upsert_contact

TOKEN_HEADER = "x-tenant-slug"


def _fake() -> FakeWhatsAppProvider:
    provider = registry.resolve("whatsapp")
    assert isinstance(provider, FakeWhatsAppProvider)
    return provider


async def _call_policy(session) -> None:
    """A test-only destination country whose call policy is open all day (no clock dependency)."""
    from app.db.models.entities import CountryCallPolicy

    row = (await session.execute(
        select(CountryCallPolicy).where(CountryCallPolicy.country == "ZY")
    )).scalar_one_or_none()
    if row is None:
        session.add(CountryCallPolicy(
            country="ZY", allowed_hours_start=0, allowed_hours_end=24,
            recording_consent_mode="explicit",
            disclosure_text_en="Hello, this is an AI assistant calling on behalf of the buyer.",
            disclosure_text_ar="مرحباً، أنا مساعد ذكاء اصطناعي أتصل نيابة عن المشتري.",
        ))
        await session.flush()


async def _scene(*, phone: str = "+201009990011", whatsapp_opt_in: bool = True,
                 country: str = "EG") -> dict:
    """A dedicated supplier + sent RFQ so each test owns its conversation."""
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        suffix = uuid.uuid4().hex[:6]
        supplier = Supplier(tenant_id=tenant.id, name=f"API Supplier {suffix}",
                            name_ar=f"مورد {suffix}", status="approved", preferred_language="ar",
                            preferred_channel="email", country=country)
        rfq = RFQ(tenant_id=tenant.id, code=f"RFQ-API-{suffix}", title=f"API RFQ {suffix}",
                  status="sent", currency="EGP", payment_terms="Net 30")
        session.add_all([supplier, rfq])
        await session.flush()
        await upsert_contact(session, tenant, supplier, name="API Contact",
                             email=f"api.{suffix}@example.com", phone=phone)
        invitation = RFQSupplier(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                                 status="sent", channel="email")
        session.add(invitation)
        await set_opt_in(session, tenant.id, supplier.id, "whatsapp",
                         "opted_in" if whatsapp_opt_in else "opted_out", source="test")
        if country != "EG":
            await _call_policy(session)
            await set_opt_in(session, tenant.id, supplier.id, "voice", "opted_in", source="test")
        await session.commit()
        return {"tenant": tenant.slug, "supplier_id": supplier.id, "rfq_id": rfq.id,
                "suffix": suffix}


# ------------------------------------------------------------------ capabilities & flags
async def test_capabilities_lists_the_channels_with_their_state(client, buyer, requester):
    response = await client.get("/api/v1/channels/capabilities", headers=buyer["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    keys = [row["channel"] for row in body["channels"]]
    assert keys[:3] == ["whatsapp", "voice", "email"], "WhatsApp is the first-class channel"
    assert body["channels"][0]["primary"] is True
    assert body["whatsapp"]["supports_templates"] is True
    assert body["whatsapp"]["provider"]
    assert body["default_order"][:3] == ["whatsapp", "voice", "email"]

    denied = await client.get("/api/v1/channels/capabilities", headers=requester["headers"])
    assert denied.status_code == 403
    assert denied.json()["required_permission"] == "channel:read"


async def test_the_feature_switches_round_trip_and_are_admin_only(client, admin, buyer):
    current = await client.get("/api/v1/settings/features", headers=admin["headers"])
    assert current.status_code == 200, current.text
    keys = {row["key"] for row in current.json()["features"]}
    assert {"whatsapp", "whatsapp_rich_messages", "voice_ai_calls", "voice_assistant",
            "voice_recording", "channel_switching"} <= keys

    patched = await client.patch("/api/v1/settings/features", headers=admin["headers"],
                                 json={"features": {"whatsapp": False}})
    assert patched.status_code == 200, patched.text
    assert next(row for row in patched.json()["features"] if row["key"] == "whatsapp")["enabled"] is False

    restored = await client.patch("/api/v1/settings/features", headers=admin["headers"],
                                  json={"features": {"whatsapp": True}})
    assert next(row for row in restored.json()["features"] if row["key"] == "whatsapp")["enabled"] is True

    denied = await client.patch("/api/v1/settings/features", headers=buyer["headers"],
                                json={"features": {"whatsapp": False}})
    assert denied.status_code == 403


# ------------------------------------------------------------------ supplier channels
async def test_supplier_channel_preferences_and_consent_are_set_through_the_api(client, buyer):
    scene = await _scene(whatsapp_opt_in=False)
    prefs = await client.patch(f"/api/v1/suppliers/{scene['supplier_id']}/channels",
                               headers=buyer["headers"],
                               json={"preferred_channel": "whatsapp",
                                     "secondary_channels": ["email", "voice"],
                                     "preferred_language": "ar"})
    assert prefs.status_code == 200, prefs.text
    assert prefs.json()["preferred_channel"] == "whatsapp"
    assert prefs.json()["secondary_channels"] == ["email", "voice"]

    state = await client.get(f"/api/v1/suppliers/{scene['supplier_id']}/channels",
                             headers=buyer["headers"])
    assert state.status_code == 200
    whatsapp = next(row for row in state.json()["channels"] if row["channel"] == "whatsapp")
    assert whatsapp["available"] is False
    assert "no_opt_in" in whatsapp["reasons"]

    opt_in = await client.post(f"/api/v1/suppliers/{scene['supplier_id']}/opt-in",
                               headers=buyer["headers"],
                               json={"channel": "whatsapp", "status": "opted_in",
                                     "evidence": "Supplier replied YES on WhatsApp"})
    assert opt_in.status_code == 200, opt_in.text
    assert opt_in.json()["status"] == "opted_in"

    after = await client.get(f"/api/v1/suppliers/{scene['supplier_id']}/channels",
                             headers=buyer["headers"])
    whatsapp_after = next(row for row in after.json()["channels"] if row["channel"] == "whatsapp")
    assert whatsapp_after["available"] is True


async def test_rfq_channels_show_one_row_per_supplier_with_its_resolved_channel(client, buyer):
    scene = await _scene()
    response = await client.get(f"/api/v1/rfqs/{scene['rfq_id']}/channels", headers=buyer["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    row = next(item for item in body["items"] if item["supplier_id"] == scene["supplier_id"])
    assert row["channel"] in {"whatsapp", "email", "voice"}
    assert row["comm_status"]
    assert row["comm_status_label_ar"]


async def test_an_override_is_validated_required_reason_is_not_needed_when_reachable(client, buyer):
    scene = await _scene()
    response = await client.put(
        f"/api/v1/rfqs/{scene['rfq_id']}/suppliers/{scene['supplier_id']}/channel",
        headers=buyer["headers"], json={"channel": "whatsapp", "reason": "Supplier asked for WhatsApp"})
    assert response.status_code == 200, response.text
    assert response.json()["channel"] == "whatsapp"
    assert response.json()["source"] == "rfq_override"

    cleared = await client.put(
        f"/api/v1/rfqs/{scene['rfq_id']}/suppliers/{scene['supplier_id']}/channel",
        headers=buyer["headers"], json={"channel": None})
    assert cleared.status_code == 200
    assert cleared.json()["source"] != "rfq_override"


# ------------------------------------------------------------------ WhatsApp outbound
async def test_sending_on_whatsapp_and_a_structured_quote_request(client, buyer):
    scene = await _scene()
    provider = _fake()
    provider.clear()

    sent = await client.post(f"/api/v1/rfqs/{scene['rfq_id']}/whatsapp", headers=buyer["headers"],
                             json={"supplier_id": scene["supplier_id"], "body": "Please quote",
                                   "locale": "ar", "kind": "text"})
    assert sent.status_code == 200, sent.text
    assert sent.json()["ok"] is True
    assert provider.last_outbound()["type"] == "text"

    quoted = await client.post(f"/api/v1/rfqs/{scene['rfq_id']}/whatsapp", headers=buyer["headers"],
                               json={"supplier_id": scene["supplier_id"], "body": "",
                                     "locale": "ar", "quote_request": True})
    assert quoted.status_code == 200, quoted.text
    assert provider.last_outbound()["type"] == "interactive"


async def test_whatsapp_cannot_be_sent_after_the_supplier_opts_out(client, buyer):
    scene = await _scene(whatsapp_opt_in=False)
    blocked = await client.post(f"/api/v1/rfqs/{scene['rfq_id']}/whatsapp", headers=buyer["headers"],
                                json={"supplier_id": scene["supplier_id"], "body": "Hello"})
    # A consent block is a policy verdict, not a transport error: the row is stored and refused.
    assert blocked.status_code == 200, blocked.text
    body = blocked.json()
    assert body["ok"] is False
    assert "BLOCK_NO_OPT_IN" in body["codes"]
    assert body["policy"]["allowed"] is False


# ------------------------------------------------------------------ webhook
async def test_the_meta_verification_handshake_echoes_the_challenge(client):
    ok = await client.get("/api/v1/inbound/whatsapp",
                          params={"hub.mode": "subscribe", "hub.verify_token": "verify-me",
                                  "hub.challenge": "12345"})
    assert ok.status_code == 200
    assert ok.text == "12345"

    bad = await client.get("/api/v1/inbound/whatsapp",
                           params={"hub.mode": "subscribe", "hub.verify_token": "wrong",
                                   "hub.challenge": "12345"})
    assert bad.status_code == 403


async def test_an_inbound_whatsapp_webhook_creates_an_extraction_and_marks_the_reply(client):
    scene = await _scene()
    text = f"{scene['suffix']} — سعر الطن 21500 جنيه، التسليم خلال 20 يوم، صلاحية العرض 30 يوم"
    payload = {
        "entry": [{"changes": [{"value": {
            "messages": [{"id": f"wamid.api-{scene['suffix']}", "from": "201009990011",
                          "type": "text", "timestamp": "1760000001", "text": {"body": text}}],
            "contacts": [{"wa_id": "201009990011", "profile": {"name": "API Supplier"}}],
        }}]}]
    }
    response = await client.post("/api/v1/inbound/whatsapp", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["processed"] == 1
    assert body["results"][0]["matched"] is True
    assert body["results"][0]["extractions"]


# ------------------------------------------------------------------ templates
async def test_the_template_library_covers_both_channels_and_both_locales(client, buyer):
    response = await client.get("/api/v1/templates/library", headers=buyer["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 20
    channels_present = {row["channel"] for row in body["channels"]}
    assert {"whatsapp", "email"} <= channels_present
    for channel in ("whatsapp", "email"):
        locales = {item["locale"] for item in body["items"][channel]}
        assert locales == {"ar", "en"}
    sample = next(item for item in body["items"]["whatsapp"] if item["locale"] == "ar")
    assert sample["kind"]
    assert sample["variables"], "a template must declare its placeholders"


# ------------------------------------------------------------------ AI voice
async def test_an_ai_voice_call_is_placed_browsed_and_audited(client, buyer, requester):
    scene = await _scene(phone="+201000000002", country="ZY")
    created = await client.post("/api/v1/voice/calls", headers=buyer["headers"],
                                json={"rfq_id": scene["rfq_id"], "supplier_id": scene["supplier_id"],
                                      "language": "ar-EG", "mode": "ai"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["ok"] is True, body
    assert body["disclosure_played"] is True
    assert body["consent_given"] is True
    assert body["ai_agent"]["guard"].startswith("no competitor data")

    listing = await client.get("/api/v1/voice/calls", headers=buyer["headers"],
                               params={"rfq_id": scene["rfq_id"]})
    assert listing.status_code == 200
    assert any(row["id"] == body["call_id"] for row in listing.json()["items"])

    detail = await client.get(f"/api/v1/voice/calls/{body['call_id']}", headers=buyer["headers"])
    assert detail.status_code == 200
    assert detail.json()["turns"]
    assert detail.json()["policy_snapshot"]["ai_agent"]

    denied = await client.post("/api/v1/voice/calls", headers=requester["headers"],
                               json={"rfq_id": scene["rfq_id"], "supplier_id": scene["supplier_id"]})
    assert denied.status_code == 403
    assert denied.json()["required_permission"] == "voice:call"


async def test_a_human_call_gets_a_bilingual_script(client, buyer):
    scene = await _scene()
    response = await client.post("/api/v1/voice/calls", headers=buyer["headers"],
                                 json={"rfq_id": scene["rfq_id"], "supplier_id": scene["supplier_id"],
                                       "mode": "human"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mode"] == "human"
    assert body["script_ar"] and body["script_en"]


async def test_the_voice_feature_flags_are_visible(client, buyer):
    response = await client.get("/api/v1/voice/features", headers=buyer["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["disclosure_required"] is True
    assert body["recording_requires_consent"] is True
    assert {row["key"] for row in body["features"]} >= {"voice_ai_calls", "voice_assistant",
                                                       "voice_recording"}


# ------------------------------------------------------------------ assistant
async def test_the_assistant_endpoint_answers_within_the_role_and_refuses_actions(client, manager,
                                                                                warehouse):
    asked = await client.post("/api/v1/assistant/ask", headers=manager["headers"],
                              json={"text": "اعتمد أفضل عرض", "locale": "ar"})
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body["blocked_code"] == "ASSISTANT_FORBIDDEN_ACTION"
    assert body["can_act"] is False

    allowed = await client.post("/api/v1/assistant/ask", headers=manager["headers"],
                                json={"text": "What can you do?", "locale": "en"})
    assert allowed.json()["intent"] == "help"

    scoped = await client.post("/api/v1/assistant/ask", headers=warehouse["headers"],
                               json={"text": "What is the outstanding balance?"})
    assert scoped.json()["blocked_code"] == "ASSISTANT_PERMISSION"

    sessions = await client.get("/api/v1/assistant/sessions", headers=manager["headers"])
    assert sessions.status_code == 200
    assert sessions.json()["items"]

    detail = await client.get(f"/api/v1/assistant/sessions/{body['session_id']}",
                              headers=manager["headers"])
    assert detail.status_code == 200
    assert detail.json()["turns"][0]["role"] == "user"


async def test_a_spoken_question_is_transcribed_then_answered(client, manager):
    response = await client.post("/api/v1/assistant/transcribe", headers=manager["headers"],
                                 params={"ask": "true", "locale": "ar"},
                                 files={"file": ("question.wav", b"RIFFfakeaudio", "audio/wav")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"]
    assert body["answer"]["ok"] is True
    assert body["answer"]["can_act"] is False
    assert body["answer"]["channel"] == "voice"


# ------------------------------------------------------------------ inbox
async def test_the_inbox_lists_threads_and_guards_reply_permissions(client, buyer, auditor,
                                                                   requester):
    scene = await _scene()
    await client.post(f"/api/v1/rfqs/{scene['rfq_id']}/whatsapp", headers=buyer["headers"],
                      json={"supplier_id": scene["supplier_id"], "body": "Hello there"})

    listing = await client.get("/api/v1/inbox", headers=buyer["headers"],
                               params={"supplier_id": scene["supplier_id"]})
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["items"], "the sent message must appear in the inbox"
    thread = body["items"][0]
    assert "whatsapp" in thread["channels_used"]

    detail = await client.get(f"/api/v1/inbox/threads/{thread['thread_id']}",
                              headers=buyer["headers"])
    assert detail.status_code == 200
    assert detail.json()["messages"]

    events = await client.get(f"/api/v1/inbox/threads/{thread['thread_id']}/events",
                              headers=buyer["headers"])
    assert events.status_code == 200
    assert any(row["kind"] == "sent" for row in events.json()["items"])

    found = await client.get("/api/v1/inbox/search", headers=buyer["headers"],
                             params={"q": "Hello there"})
    assert found.status_code == 200
    assert found.json()["total"] >= 1

    # The auditor may read the record but not speak to the supplier.
    read_only = await client.get("/api/v1/inbox", headers=auditor["headers"])
    assert read_only.status_code == 200
    denied = await client.post(f"/api/v1/inbox/threads/{thread['thread_id']}/reply",
                               headers=auditor["headers"], json={"body": "Hello"})
    assert denied.status_code == 403
    assert denied.json()["required_permission"] == "inbox:reply"

    forbidden = await client.get("/api/v1/inbox", headers=requester["headers"])
    assert forbidden.status_code == 403


async def test_a_reply_from_the_inbox_is_sent_and_audited(client, buyer):
    scene = await _scene()
    provider = _fake()
    await client.post(f"/api/v1/rfqs/{scene['rfq_id']}/whatsapp", headers=buyer["headers"],
                      json={"supplier_id": scene["supplier_id"], "body": "First contact"})
    listing = await client.get("/api/v1/inbox", headers=buyer["headers"],
                               params={"supplier_id": scene["supplier_id"]})
    thread_id = listing.json()["items"][0]["thread_id"]
    provider.clear()

    reply = await client.post(f"/api/v1/inbox/threads/{thread_id}/reply", headers=buyer["headers"],
                              json={"body": "Any update on the quotation?", "locale": "en"})
    assert reply.status_code == 200, reply.text
    assert reply.json()["ok"] is True
    assert provider.last_outbound()["type"] in {"text", "template"}


# ------------------------------------------------------------------ feature flags off
async def test_every_gated_endpoint_fails_closed_when_its_flag_is_off(client, admin, buyer):
    """Flags are tenant-wide and fail closed: the same call that worked above is refused, not errored."""
    scene = await _scene(whatsapp_opt_in=True, country="ZY")

    async def _set(**patch):
        response = await client.patch("/api/v1/settings/features", headers=admin["headers"],
                                     json={"features": patch})
        assert response.status_code == 200, response.text

    try:
        await _set(whatsapp=False, whatsapp_rich_messages=False)
        blocked_wa = await client.post(f"/api/v1/rfqs/{scene['rfq_id']}/whatsapp",
                                       headers=buyer["headers"],
                                       json={"supplier_id": scene["supplier_id"], "body": "Hello"})
        assert blocked_wa.status_code == 200, blocked_wa.text
        assert blocked_wa.json()["ok"] is False
        assert "BLOCK_FEATURE_OFF" in blocked_wa.json()["codes"]

        await _set(voice_ai_calls=False)
        blocked_voice = await client.post("/api/v1/voice/calls", headers=buyer["headers"],
                                          json={"rfq_id": scene["rfq_id"],
                                                "supplier_id": scene["supplier_id"], "mode": "ai"})
        assert blocked_voice.json()["ok"] is False
        assert blocked_voice.json()["codes"] == ["BLOCK_FEATURE_OFF"]

        await _set(voice_assistant=False)
        blocked_assistant = await client.post("/api/v1/assistant/ask", headers=buyer["headers"],
                                              json={"text": "What can you do?"})
        assert blocked_assistant.status_code == 200
        assert blocked_assistant.json()["codes"] == ["BLOCK_FEATURE_OFF"]

        await _set(channel_switching=False)
        blocked_switch = await client.post(
            f"/api/v1/rfqs/{scene['rfq_id']}/suppliers/{scene['supplier_id']}/switch-channel",
            headers=buyer["headers"], json={"channel": "whatsapp"})
        assert blocked_switch.status_code == 422, blocked_switch.text
        assert blocked_switch.json()["code"] == "BLOCK_FEATURE_OFF"
    finally:
        await _set(whatsapp=True, whatsapp_rich_messages=True, voice_ai_calls=True,
                   voice_assistant=True, channel_switching=True)

    # ... and the capability comes straight back when the flag is restored.
    working = await client.post(f"/api/v1/rfqs/{scene['rfq_id']}/whatsapp", headers=buyer["headers"],
                                json={"supplier_id": scene["supplier_id"], "body": "Restored"})
    assert working.json()["ok"] is True
