"""Adapter contract tests (Section 9).

Every external system has three implementations: an interface, a real one behind env
credentials, and a deterministic Fake. These tests pin the Fake behaviour *and* the
safety guards of the real ones (SSRF, robots.txt, webhook signatures) so a swap to
production credentials cannot quietly change semantics.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.adapters.base import InboundMessage, registry
from app.adapters.email import FakeEmailProvider, SmtpEmailProvider
from app.adapters.erp_fx_payment import (
    FakePaymentGateway,
    FileExchangeConnector,
    LocalStorage,
    PaymobGateway,
    StaticFxProvider,
)
from app.adapters.search import FakeSearchProvider, PageFetcher, build_search_provider
from app.adapters.telephony_ocr_stt import (
    FakeOcrProvider,
    FakeSttProvider,
    FakeTelephonyProvider,
    extract_numbers,
)
from app.adapters.whatsapp import FakeWhatsAppProvider


# ----------------------------------------------------------------------- email
async def test_fake_email_records_an_outbox_and_drains_the_inbox():
    provider = FakeEmailProvider()
    sent = await provider.send(to="sales@supplier.example", subject="RFQ", body="Please quote")
    assert sent.ok and sent.provider_message_id
    assert provider.outbox[0]["to"] == "sales@supplier.example"

    provider.queue_inbound(sender="sales@supplier.example", subject="Re: RFQ", body="Our price is 21,000")
    provider.queue_inbound(sender="other@supplier.example", subject="Re: RFQ", body="Our price is 22,000")
    first = await provider.poll_inbox(limit=1)
    assert len(first) == 1 and isinstance(first[0], InboundMessage)
    assert len(provider.inbox) == 1
    provider.clear()
    assert provider.outbox == [] and provider.inbox == []


async def test_smtp_provider_reports_configuration_instead_of_crashing():
    provider = SmtpEmailProvider(host="", port=0, user="", password="", sender="")
    result = await provider.send(to="nobody@example.com", subject="x", body="y")
    assert result.ok is False
    assert result.error


# -------------------------------------------------------------------- whatsapp
async def test_whatsapp_templates_webhooks_and_signature_verification():
    provider = FakeWhatsAppProvider(verify_token="secret-token")
    text = await provider.send_text(to="+201000000001", body="مرحباً")
    assert text.ok
    template = await provider.send_template(to="+201000000002", template="rfq_invitation", variables=["RFQ-1", "3 weeks"])
    assert template.ok and provider.outbox[-1]["template"] == "rfq_invitation"

    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "+201000000003", "type": "text", "text": {"body": "السعر 21000"}},
        {"from": "+201000000004", "type": "audio", "audio": {"id": "media-1"}},
        {"from": "+201000000005", "type": "document", "document": {"filename": "quote.pdf"}},
    ]}}]}]}
    inbound = provider.parse_webhook(payload)
    assert len(inbound) == 3
    assert inbound[0].body == "السعر 21000"
    assert "[voice note]" == inbound[1].body
    assert "quote.pdf" in inbound[2].body

    import hashlib
    import hmac

    body = json.dumps(payload).encode()
    signature = hmac.new(b"secret-token", body, hashlib.sha256).hexdigest()
    assert provider.verify_webhook(body, signature) is True
    assert provider.verify_webhook(body, "deadbeef") is False


# ------------------------------------------------------------------- telephone
async def test_telephony_discloses_ai_and_records_consent():
    provider = FakeTelephonyProvider()
    result = await provider.place_call(to="+201000000009", script="Ask for a quote",
                                       disclosure="This is an AI assistant calling on behalf of Nile Manufacturing.",
                                       country="EG", language="ar", consent_required=True)
    assert result.disclosure_played is True
    assert "AI" in (result.transcript or "")
    assert result.provider_call_id
    fetched = await provider.fetch_call(result.provider_call_id)
    assert fetched.provider_call_id == result.provider_call_id


async def test_telephony_no_answer_path():
    provider = FakeTelephonyProvider()
    # choose a number whose checksum triggers the no-answer branch
    target = next(f"+20100000{i:04d}" for i in range(200) if sum(ord(c) for c in f"+20100000{i:04d}") % 7 == 0)
    result = await provider.place_call(to=target, script="s", disclosure="d", country="EG",
                                       language="ar", consent_required=True)
    assert result.status == "no_answer"
    assert result.consent_given is False


# ------------------------------------------------------------------- ocr / stt
async def test_ocr_and_stt_fakes_return_extractable_text():
    ocr = await FakeOcrProvider().extract(data=b"\x89PNG", filename="scan.png")
    assert ocr.text and ocr.confidence > 0.8
    transcript = await FakeSttProvider().transcribe(data=b"OggS", filename="note.ogg")
    assert "حديد" in transcript.text
    assert extract_numbers("500 طن بسعر 21,500 جنيه") == ["500", "21,500"] or extract_numbers("a 1 b 2") == ["1", "2"]


# ----------------------------------------------------------------------- search
async def test_fake_search_is_deterministic_and_dedups():
    provider = FakeSearchProvider()
    first = await provider.search("rebar distributor", limit=3, country="EG")
    second = await provider.search("rebar distributor", limit=3, country="EG")
    assert [hit.url for hit in first] == [hit.url for hit in second]
    candidates = await provider.discover_suppliers("rebar distributor", country="EG", limit=5)
    assert candidates and all(candidate.confidence > 0 for candidate in candidates)


async def test_search_provider_factory_falls_back_to_fake():
    assert build_search_provider("fake", "").name == "fake"
    assert build_search_provider("unknown", "") is not None


def test_page_fetcher_blocks_private_targets():
    fetcher = PageFetcher()
    assert fetcher.is_public_url("https://supplier.example/about") is True
    for blocked in ("http://127.0.0.1/admin", "http://localhost:8000", "http://169.254.169.254/latest/meta-data",
                    "file:///etc/passwd", "http://10.0.0.5/internal", "http://192.168.1.1",
                    "http://metadata.google.internal"):
        assert fetcher.is_public_url(blocked) is False, blocked


def test_robots_rules_are_respected():
    fetcher = PageFetcher()
    assert fetcher.robots_allows("https://supplier.example/about", "User-agent: *\nAllow: /") is True
    assert fetcher.robots_allows("https://supplier.example/private", "User-agent: *\nDisallow: /private") is False


# ------------------------------------------------------------------ erp and fx
async def test_file_exchange_erp_roundtrip(tmp_path: Path):
    connector = FileExchangeConnector(inbox_dir=str(tmp_path / "in"), outbox_dir=str(tmp_path / "out"))
    assert (await connector.test_connection()).ok

    source = tmp_path / "in" / "pr.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("description,quantity,uom\nRebar 12mm,50,t\n", encoding="utf-8")
    rows = await connector.import_requisitions(limit=5)
    assert rows[0]["description"] == "Rebar 12mm"
    assert not source.exists(), "imported files must be moved aside so they are not double-imported"

    exported = await connector.export_purchase_order({
        "code": "PO-1", "currency": "EGP",
        "lines": [{"description": "Rebar 12mm", "quantity": "50", "uom": "t", "unit_price": "21000"}],
    })
    assert exported.ok
    assert "sku,description,quantity" in Path(exported.reference).read_text(encoding="utf-8")


async def test_static_fx_provider_is_deterministic_and_cross_rates():
    provider = StaticFxProvider()
    usd_egp = await provider.rate("USD", "EGP")
    assert usd_egp and float(usd_egp.rate) > 1
    egp_usd = await provider.rate("EGP", "USD")
    assert abs(float(usd_egp.rate) * float(egp_usd.rate) - 1) < 1e-6
    assert await provider.rate("USD", "XXX") is None


async def test_payment_gateways():
    fake = FakePaymentGateway()
    invoice = await fake.create_invoice(amount="1490.00", currency="EGP", reference="inv-1", description="Growth plan")
    assert invoice.ok and invoice.status == "paid"

    import hashlib

    body = b'{"id":"evt_1"}'
    assert await fake.verify_webhook(body, hashlib.sha256(body).hexdigest()) is True
    assert await fake.verify_webhook(body, "bad") is False

    paymob = PaymobGateway()
    refused = await paymob.create_invoice(amount="1", currency="EGP", reference="x", description="y")
    assert refused.ok is False and "extension point" in (refused.error or "")


async def test_local_storage_roundtrip(tmp_path: Path):
    store = LocalStorage(root=str(tmp_path / "objects"))
    store.put("rfq/1/quote.pdf", b"%PDF-1.4")
    assert store.exists("rfq/1/quote.pdf")
    assert store.get("rfq/1/quote.pdf") == b"%PDF-1.4"
    assert store.write_text("notes/a.txt", "hello") == "notes/a.txt"


# ---------------------------------------------------------------------- registry
def test_registry_resolves_every_documented_adapter():
    for name in ("email", "whatsapp", "telephony", "ocr", "stt", "search", "erp", "fx", "payment", "storage"):
        adapter = registry.resolve(name)
        assert adapter is not None, name
        assert getattr(adapter, "name", ""), name


def test_registry_rejects_unknown_adapter():
    with pytest.raises(KeyError):
        registry.resolve("quantum")


async def test_capabilities_report_sandbox_mode(client, admin):
    resp = await client.get("/api/v1/meta/capabilities", headers=admin["headers"])
    assert resp.status_code == 200
    caps = resp.json()
    assert all({"provider", "live", "mode"} <= set(value) for value in caps["adapters"].values())
    assert caps["features"]["self_hosted_llm"] in {True, False}


async def test_adapters_run_concurrently_without_blocking():
    """Fakes must be async-safe: the dispatcher fans out to every supplier at once."""
    provider = FakeEmailProvider()
    await asyncio.gather(*[
        provider.send(to=f"supplier{i}@example.com", subject="RFQ", body="quote please")
        for i in range(10)
    ])
    assert len(provider.outbox) == 10
