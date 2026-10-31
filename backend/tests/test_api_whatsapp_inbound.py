"""The WhatsApp inbound webhook: which tenant, which pipeline, and what is refused.

A message to the business number used to open nothing. Three separate reasons, and this module pins
all three fixes:

1. **Tenant resolution.** Meta cannot send a custom header, so `x-tenant-slug` defaulted to `nile` and
   every webhook landed on the demo tenant. The tenant is now resolved from the `phone_number_id` in
   the payload (the same id the Cloud adapter sends from), and an unknown number is answered
   `200 {processed: 0}` — never guessed, never a 404 that makes Meta retry for days — with an audit row.
2. **Signature failures were silent.** A missing or wrong `X-Hub-Signature-256` answers 401, logs one
   line with the reason, and records `channel.signature_rejected`; a strict deployment now refuses to
   boot without `WHATSAPP_APP_SECRET`.
3. **Buyer intake.** A message from a colleague of the tenant (matched on the profile phone, in any of
   the Egyptian formats) becomes a purchase request through the *same* text-intake service the
   `/intake/text` endpoint uses, is audited as `intake.whatsapp`, and gets a WhatsApp reply with the
   request code or the clarification question. A supplier replying to an open RFQ still goes down the
   extraction path unchanged.

Everything here drives the real endpoint and the real services; only the WhatsApp provider is the
deterministic fake, and the signed payloads are built exactly like `tests/test_api_channels.py` does.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.providers import ProviderNotConfigured, assert_ready
from app.db.models.entities import (
    AuditLog,
    ChannelEvent,
    IntegrationConfig,
    PurchaseRequisition,
    Tenant,
    User,
)
from app.db.session import SessionLocal
from app.services import whatsapp_channel
from app.services.phone import normalise, same_number


def _free_phone() -> str:
    """A unique Egyptian mobile in E.164: 20 + 10 national digits (`1` + nine)."""
    return f"+2011{uuid.uuid4().int % 10**8:08d}"


def _suffix() -> str:
    return uuid.uuid4().hex[:6]


async def _tenant() -> Tenant:
    async with SessionLocal() as session:
        return (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()


async def _register_number(tenant_id: str, number_id: str, *, provider: str = "fake",
                           enabled: bool = True) -> str:
    """What the Integrations screen does: store the phone number id for this tenant."""
    async with SessionLocal() as session:
        row = IntegrationConfig(tenant_id=tenant_id, kind="whatsapp", name="WhatsApp",
                                config={"provider": provider, "phone_number_id": number_id},
                                enabled=enabled, status="configured")
        session.add(row)
        await session.commit()
        return row.id


@pytest.fixture(autouse=True)
async def _clean_integrations():
    """A stored integration changes what every later test resolves, so always drop what we created.

    The adapter singletons are cleared too: one test switches the platform to the Cloud adapter to
    check signatures, and the cache would otherwise keep that instance for every test that follows.
    """
    from app.adapters.base import registry

    registry.clear_overrides()
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one_or_none()
        if tenant is not None:
            for row in (await session.execute(
                select(IntegrationConfig).where(IntegrationConfig.tenant_id == tenant.id)
            )).scalars().all():
                await session.delete(row)
            await session.commit()
    yield
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one_or_none()
        if tenant is not None:
            for row in (await session.execute(
                select(IntegrationConfig).where(IntegrationConfig.tenant_id == tenant.id)
            )).scalars().all():
                await session.delete(row)
            await session.commit()
        for user in (await session.execute(select(User).where(User.phone != ""))).scalars().all():
            if user.email.startswith("wa-pilot-"):
                user.phone = ""
                user.is_active = False
        await session.commit()
    registry.clear_overrides()


def _meta_payload(*, number_id: str, sender: str, text: str, message_id: str = "") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-1", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "201000000000", "phone_number_id": number_id},
            "contacts": [{"wa_id": sender.lstrip("+"), "profile": {"name": "Sender"}}],
            "messages": [{"id": message_id or f"wamid.{_suffix()}", "from": sender.lstrip("+"),
                          "type": "text", "timestamp": "1760000000", "text": {"body": text}}],
        }}]}],
    }


def _signed(payload: dict, secret: str) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"x-hub-signature-256": f"sha256={digest}", "content-type": "application/json"}


async def _post_meta(client, payload: dict, *, secret: str = "", headers: dict | None = None):
    merged = {"content-type": "application/json"}
    if secret:
        body, signed_headers = _signed(payload, secret)
        merged.update(signed_headers)
    else:
        body = json.dumps(payload).encode()
    merged.update(headers or {})
    return await client.post("/api/v1/inbound/whatsapp", content=body, headers=merged)


# ------------------------------------------------------------------ 1. tenant resolution
async def test_a_known_phone_number_id_reaches_its_tenant(client, buyer):
    """The payload alone decides the tenant: no header, no default."""
    tenant = await _tenant()
    number_id = f"pnid-{_suffix()}"
    await _register_number(tenant.id, number_id)
    supplier_phone = _free_phone()
    rfq_code = f"RFQ-WA-{_suffix()}"

    # A supplier scene that lives in the nile tenant; the reply must match, proving which tenant ran.
    from app.db.models.entities import RFQ, RFQSupplier, Supplier
    from app.services.suppliers import upsert_contact

    async with SessionLocal() as session:
        rfq = RFQ(tenant_id=tenant.id, code=rfq_code, title=f"WhatsApp {rfq_code}", status="sent",
                  currency="EGP", payment_terms="Net 30")
        supplier = Supplier(tenant_id=tenant.id, name=f"WA Supplier {rfq_code}", status="approved")
        session.add_all([rfq, supplier])
        await session.flush()
        await upsert_contact(session, tenant, supplier, name="Contact", email=f"{_suffix()}@example.com",
                             phone=supplier_phone)
        session.add(RFQSupplier(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                                status="invited", channel="whatsapp"))
        await session.commit()

    response = await _post_meta(client, _meta_payload(
        number_id=number_id, sender=supplier_phone,
        text=f"{rfq_code} — unit price 20500 EGP per ton, lead time 21 days, valid 14 days"))
    assert response.status_code == 200, response.text
    assert response.json()["processed"] == 1
    assert response.json()["results"][0]["matched"] is True


async def test_an_unknown_phone_number_id_is_answered_and_recorded(client):
    response = await _post_meta(client, _meta_payload(number_id=f"pnid-{_suffix()}",
                                                     sender=_free_phone(), text="مرحباً"))
    assert response.status_code == 200, response.text
    assert response.json()["processed"] == 0
    assert response.json()["reason"] == "unknown_phone_number_id"
    async with SessionLocal() as session:
        row = (await session.execute(
            select(AuditLog).where(AuditLog.action == "channel.unknown_recipient")
            .order_by(AuditLog.created_at.desc())
        )).scalars().first()
    assert row is not None and row.after["reason"] == "unknown_phone_number_id"
    assert "phone_number_id" in row.after


async def test_two_tenants_with_different_numbers_do_not_cross(client):
    """The same payload shape for two numbers must resolve to two different tenants."""
    from app.services.bootstrap import ensure_tenant

    async with SessionLocal() as session:
        other = await ensure_tenant(session, name="Delta Foods", slug=f"delta-{_suffix()}")
        await session.commit()
        other_id, nile = other.id, (await session.execute(
            select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
    nile_number, delta_number = f"pnid-{_suffix()}", f"pnid-{_suffix()}"
    await _register_number(nile.id, nile_number)
    await _register_number(other_id, delta_number)

    async with SessionLocal() as session:
        assert (await whatsapp_channel.tenant_for_phone_number_id(session, nile_number)).id == nile.id
        assert (await whatsapp_channel.tenant_for_phone_number_id(session, delta_number)).id == other_id
        assert await whatsapp_channel.tenant_for_phone_number_id(session, "pnid-nobody") is None


async def test_a_disabled_integration_receives_nothing(client):
    tenant = await _tenant()
    number_id = f"pnid-{_suffix()}"
    await _register_number(tenant.id, number_id, enabled=False)
    async with SessionLocal() as session:
        assert await whatsapp_channel.tenant_for_phone_number_id(session, number_id) is None


# ------------------------------------------------------------------ 2. signatures
async def test_a_bad_signature_is_refused_with_the_reason_recorded(client, monkeypatch):
    from app.adapters.base import registry

    tenant = await _tenant()
    number_id = f"pnid-{_suffix()}"
    await _register_number(tenant.id, number_id, provider="cloud_api")
    monkeypatch.setattr(settings, "whatsapp_provider", "cloud_api")
    monkeypatch.setattr(settings, "whatsapp_token", "meta-token")
    monkeypatch.setattr(settings, "whatsapp_phone_id", number_id)
    monkeypatch.setattr(settings, "whatsapp_app_secret", "the-app-secret")
    # The adapter cache would otherwise keep handing out the fake that earlier tests resolved.
    registry.clear_overrides()

    payload = _meta_payload(number_id=number_id, sender=_free_phone(), text="hello")
    good = await _post_meta(client, payload, secret="the-app-secret")
    assert good.status_code == 200, good.text

    bad = await _post_meta(client, payload, secret="wrong-secret")
    assert bad.status_code == 401
    async with SessionLocal() as session:
        row = (await session.execute(
            select(AuditLog).where(AuditLog.action == "channel.signature_rejected")
            .order_by(AuditLog.created_at.desc())
        )).scalars().first()
    assert row is not None and row.after["reason"] == "bad_signature"
    # The body and the secret never reach the audit trail.
    assert "the-app-secret" not in json.dumps(row.after)
    assert "hello" not in json.dumps(row.after)


def test_a_cloud_api_deployment_without_the_app_secret_refuses_to_boot(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allow_fake_providers", False)
    monkeypatch.setattr(settings, "whatsapp_provider", "cloud_api")
    monkeypatch.setattr(settings, "whatsapp_token", "meta-token")
    monkeypatch.setattr(settings, "whatsapp_phone_id", "pnid-1")
    monkeypatch.setattr(settings, "whatsapp_app_secret", "")
    with pytest.raises(ProviderNotConfigured) as exc:
        assert_ready(settings)
    assert "WHATSAPP_APP_SECRET" in str(exc.value)


# ------------------------------------------------------------------ 3. buyer intake
async def _colleague(phone: str, *, tenant_id: str) -> str:
    """An active internal user carrying a phone number in the *local* Egyptian format."""
    email = f"wa-pilot-{_suffix()}@nile.example.com"
    async with SessionLocal() as session:
        session.add(User(tenant_id=tenant_id, email=email, full_name="WhatsApp Buyer",
                         primary_role="buyer", roles=["buyer"], is_active=True,
                         # stored the way a person types it: 01xxxxxxxxx
                         phone=f"0{phone.removeprefix('+20')}"))
        await session.commit()
    return email


async def test_a_colleagues_message_becomes_a_purchase_request_and_gets_a_reply(client):
    from app.adapters.base import registry
    from app.adapters.whatsapp import FakeWhatsAppProvider

    tenant = await _tenant()
    number_id = f"pnid-{_suffix()}"
    await _register_number(tenant.id, number_id)
    phone = _free_phone()
    await _colleague(phone, tenant_id=tenant.id)
    fake = registry.resolve("whatsapp")
    assert isinstance(fake, FakeWhatsAppProvider)
    fake.clear()

    response = await _post_meta(client, _meta_payload(
        number_id=number_id, sender=phone,
        text="Need 30 t rebar 16mm for the Sadat plant, delivery within 2 weeks, target 21000 EGP per ton"))
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["handled_as"] == "buyer_intake", result
    assert result["pr_code"].startswith("PR-")

    async with SessionLocal() as session:
        pr = (await session.execute(
            select(PurchaseRequisition).where(PurchaseRequisition.id == result["pr_id"])
        )).scalar_one()
        assert pr.tenant_id == tenant.id
        assert pr.source == "whatsapp"
        assert pr.requested_by, "the request must belong to the colleague who sent it"
        trail = (await session.execute(
            select(AuditLog).where(AuditLog.action == "intake.whatsapp",
                                   AuditLog.entity_id == pr.id)
        )).scalars().first()
    assert trail is not None

    sent = fake.last_outbound()
    assert sent["to"] == normalise(phone)
    assert result["pr_code"] in sent["body"]
    assert "\n" in sent["body"], "the acknowledgement is bilingual (Arabic + English)"


async def test_a_voice_note_from_a_colleague_goes_through_the_stt_path(client):
    from app.adapters.base import registry
    from app.adapters.whatsapp import FakeWhatsAppProvider

    tenant = await _tenant()
    number_id = f"pnid-{_suffix()}"
    await _register_number(tenant.id, number_id)
    phone = _free_phone()
    await _colleague(phone, tenant_id=tenant.id)
    fake = registry.resolve("whatsapp")
    assert isinstance(fake, FakeWhatsAppProvider)
    fake.clear()
    media_id = f"media-{_suffix()}"
    fake.media[media_id] = b"fake-ogg-bytes"

    payload = {
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": number_id},
            "messages": [{"id": f"wamid.{_suffix()}", "from": phone.lstrip("+"), "type": "audio",
                          "timestamp": "1760000000",
                          "audio": {"id": media_id, "mime_type": "audio/ogg",
                                    "filename": "voice.ogg"}}],
            "contacts": [{"wa_id": phone.lstrip("+"), "profile": {"name": "Buyer"}}],
        }}]}]
    }
    response = await _post_meta(client, payload)
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["handled_as"] == "buyer_intake", result
    assert result["pr_code"]


async def test_an_unknown_sender_creates_nothing_and_gets_no_reply(client):
    from app.adapters.base import registry
    from app.adapters.whatsapp import FakeWhatsAppProvider

    tenant = await _tenant()
    number_id = f"pnid-{_suffix()}"
    await _register_number(tenant.id, number_id)
    fake = registry.resolve("whatsapp")
    assert isinstance(fake, FakeWhatsAppProvider)
    fake.clear()

    async with SessionLocal() as session:
        before = len((await session.execute(select(PurchaseRequisition))).scalars().all())

    response = await _post_meta(client, _meta_payload(
        number_id=number_id, sender=_free_phone(), text="أريد شراء 10 أطنان أسمنت"))
    assert response.status_code == 200, response.text
    assert response.json()["results"][0].get("handled_as") != "buyer_intake"
    assert fake.outbox == [], "an unknown sender must not receive an automatic reply"
    async with SessionLocal() as session:
        after = len((await session.execute(select(PurchaseRequisition))).scalars().all())
        event = (await session.execute(
            select(ChannelEvent).where(ChannelEvent.kind == "received_unmatched")
            .order_by(ChannelEvent.created_at.desc())
        )).scalars().first()
    assert after == before
    assert event is not None


async def test_a_supplier_replying_to_an_open_rfq_still_takes_the_extraction_path(client):
    from app.db.models.entities import RFQ, RFQSupplier, Supplier
    from app.services.suppliers import upsert_contact

    tenant = await _tenant()
    number_id = f"pnid-{_suffix()}"
    await _register_number(tenant.id, number_id)
    phone = _free_phone()
    code = f"RFQ-SUP-{_suffix()}"
    async with SessionLocal() as session:
        rfq = RFQ(tenant_id=tenant.id, code=code, title=code, status="sent", currency="EGP",
                  payment_terms="Net 30")
        supplier = Supplier(tenant_id=tenant.id, name=f"Supplier {code}", status="approved")
        session.add_all([rfq, supplier])
        await session.flush()
        await upsert_contact(session, tenant, supplier, name="Sales", email=f"{_suffix()}@example.com",
                             phone=phone)
        session.add(RFQSupplier(tenant_id=tenant.id, rfq_id=rfq.id, supplier_id=supplier.id,
                                status="invited", channel="whatsapp"))
        await session.commit()

    response = await _post_meta(client, _meta_payload(
        number_id=number_id, sender=phone,
        text=f"{code} — price 20900 EGP per ton, lead time 18 days, valid 20 days"))
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["matched"] is True
    assert result.get("handled_as") != "buyer_intake"
    assert result["extractions"]


async def test_a_colleague_who_is_also_a_supplier_is_read_as_a_request_when_no_rfq_is_open(client):
    from app.db.models.entities import Supplier
    from app.services.suppliers import upsert_contact

    tenant = await _tenant()
    number_id = f"pnid-{_suffix()}"
    await _register_number(tenant.id, number_id)
    phone = _free_phone()
    await _colleague(phone, tenant_id=tenant.id)
    async with SessionLocal() as session:
        supplier = Supplier(tenant_id=tenant.id, name=f"Dual {_suffix()}", status="approved")
        session.add(supplier)
        await session.flush()
        await upsert_contact(session, tenant, supplier, name="Dual", email=f"{_suffix()}@example.com",
                             phone=phone)
        await session.commit()

    response = await _post_meta(client, _meta_payload(
        number_id=number_id, sender=phone,
        text="Please order 5 t cement for the Maadi site, target 1200 EGP per ton"))
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["handled_as"] == "buyer_intake"


# ------------------------------------------------------------------ 4. number formats
@pytest.mark.parametrize("value", [
    "01012345678", "+201012345678", "201012345678", "00201012345678",
    "0101 234 5678", "(+20) 101-234-5678", "  +20 101 234 5678  ",
])
def test_every_egyptian_format_normalises_to_the_same_number(value):
    assert normalise(value) == "201012345678"


@pytest.mark.parametrize(("left", "right"), [
    ("01012345678", "201012345678"),
    ("+201012345678", "01012345678"),
    ("00201012345678", "+201012345678"),
    ("01012345678", "0101 234 5678"),
])
def test_the_same_line_written_differently_compares_equal(left, right):
    assert same_number(left, right) is True


@pytest.mark.parametrize(("left", "right"), [
    ("01012345678", "01012345679"),
    ("", "01012345678"),
    (None, "201012345678"),
])
def test_different_or_missing_numbers_do_not_match(left, right):
    assert same_number(left, right) is False
