"""Twilio transport proven over a real HTTP round trip (local server, real client, no mocks).

The tests below start a small ASGI application on a real port and point `TwilioWhatsAppProvider` at
it. The production code path is therefore exercised unchanged: httpx opens a socket, sends basic auth
and an `application/x-www-form-urlencoded` body, reads the JSON answer, and maps failures. Only the
vendor's server is replaced by one we control — nothing inside the adapter is patched.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
from urllib.parse import parse_qs

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.adapters.twilio import (
    TwilioSmsProvider,
    TwilioWhatsAppProvider,
    normalise_whatsapp,
    whatsapp_address,
)

SID = "AC_test_account_sid"
TOKEN = "test_auth_token"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Recorder:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.calls: list[dict] = []
        self.auth: list[tuple[str, str]] = []
        self.behaviour: list[int] = []  # status codes to return, consumed in order
        self.call_behaviour: list[int] = []

    def next_status(self) -> int:
        return self.behaviour.pop(0) if self.behaviour else 201


@contextlib.asynccontextmanager
async def twilio_stub(recorder: Recorder):
    """A real HTTP server that answers like Twilio's Messages endpoint."""
    import uvicorn

    app = FastAPI()

    @app.post("/2010-04-01/Accounts/{sid}/Messages.json")
    async def messages(sid: str, request: Request):  # noqa: ANN001
        body = await request.body()
        recorder.requests.append({key: values[-1] for key, values in
                                  parse_qs(body.decode()).items()})
        auth = request.headers.get("authorization", "")
        import base64

        decoded = base64.b64decode(auth.split(" ", 1)[1]).decode() if auth.startswith("Basic ") else ""
        recorder.auth.append(tuple(decoded.split(":", 1)) if ":" in decoded else ("", ""))
        status = recorder.next_status()
        if status >= 400:
            return JSONResponse(status_code=status,
                                content={"code": 21211, "message": "The 'To' number is not valid."})
        return JSONResponse(status_code=201,
                            content={"sid": f"SM{len(recorder.requests):04d}", "status": "queued"})

    @app.post("/2010-04-01/Accounts/{sid}/Calls.json")
    async def calls(sid: str, request: Request):  # noqa: ANN001
        body = await request.body()
        recorder.calls.append({key: values[-1] for key, values in parse_qs(body.decode()).items()})
        status = recorder.call_behaviour.pop(0) if recorder.call_behaviour else 201
        if status >= 400:
            return JSONResponse(status_code=status,
                                content={"code": 21215, "message": "Account not authorized to call this number."})
        return JSONResponse(status_code=201, content={"sid": "CA1234", "status": "queued"})

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=5)


@pytest.fixture
async def stub():
    recorder = Recorder()
    async with twilio_stub(recorder) as base:
        yield base, recorder


# ------------------------------------------------------------------ addressing
def test_numbers_are_normalised_the_same_way_whatever_the_caller_passes():
    assert normalise_whatsapp("+201000000001") == "+201000000001"
    assert normalise_whatsapp("whatsapp:+201000000001") == "+201000000001"
    assert normalise_whatsapp("whatsapp:201000000001") == "+201000000001"
    assert normalise_whatsapp(" +20 100 000 0001 ") == "+201000000001"
    assert whatsapp_address("201000000001") == "whatsapp:+201000000001"


# ------------------------------------------------------------------ sending
async def test_whatsapp_text_is_sent_with_basic_auth_and_the_whatsapp_prefix(stub):
    base, recorder = stub
    provider = TwilioWhatsAppProvider(SID, TOKEN, "whatsapp:+14155238886", base_url=base)

    result = await provider.send_text(to="+201000000001", body="استلمنا عرضكم، شكراً")

    assert result.ok is True
    assert result.provider_message_id == "SM0001"
    assert recorder.auth[0] == (SID, TOKEN)
    sent = recorder.requests[0]
    assert sent["To"] == "whatsapp:+201000000001"
    assert sent["From"] == "whatsapp:+14155238886"
    assert sent["Body"] == "استلمنا عرضكم، شكراً"


async def test_a_content_template_is_sent_as_contentsid_with_numbered_variables(stub):
    base, recorder = stub
    provider = TwilioWhatsAppProvider(SID, TOKEN, "whatsapp:+14155238886", base_url=base)

    await provider.send_template(to="whatsapp:+201000000002", template="HXtemplate01",
                                 variables=["Nile Steel", "RFQ-2026-0001"])

    sent = recorder.requests[0]
    assert sent["ContentSid"] == "HXtemplate01"
    assert sent["ContentVariables"] == '{"1": "Nile Steel", "2": "RFQ-2026-0001"}'


async def test_a_retryable_429_is_retried_and_then_succeeds(stub):
    base, recorder = stub
    recorder.behaviour = [429, 201]
    provider = TwilioWhatsAppProvider(SID, TOKEN, "whatsapp:+14155238886", base_url=base,
                                      max_retries=2)

    result = await provider.send_text(to="+201000000003", body="reminder")

    assert result.ok is True
    assert len(recorder.requests) == 2, "429 must be retried exactly once here"


async def test_a_bad_number_is_not_retried_and_the_error_is_returned(stub):
    base, recorder = stub
    recorder.behaviour = [400]
    provider = TwilioWhatsAppProvider(SID, TOKEN, "whatsapp:+14155238886", base_url=base,
                                      max_retries=3)

    result = await provider.send_text(to="+20invalid", body="hello")

    assert result.ok is False
    assert "21211" in result.error and "not valid" in result.error
    assert len(recorder.requests) == 1, "a 4xx will stay a 4xx; retrying it wastes money"


async def test_interactive_degrades_to_a_numbered_text_message(stub):
    base, recorder = stub
    provider = TwilioWhatsAppProvider(SID, TOKEN, "whatsapp:+14155238886", base_url=base)

    await provider.send_interactive(to="+201000000004", body="Choose a delivery window",
                                    options=["Sunday", "Monday"])

    body = recorder.requests[0]["Body"]
    assert "1. Sunday" in body and "2. Monday" in body


async def test_sms_uses_the_plain_number_and_the_sms_sender(stub):
    base, recorder = stub
    provider = TwilioSmsProvider(SID, TOKEN, "+14155238886", base_url=base)

    result = await provider.send_text(to="whatsapp:+201000000005", body="OTP 1234")

    assert result.ok is True
    assert recorder.requests[0]["To"] == "+201000000005"
    assert recorder.requests[0]["From"] == "+14155238886"


async def test_a_missing_credential_fails_at_construction_not_at_send_time():
    with pytest.raises(ValueError, match="TWILIO_ACCOUNT_SID"):
        TwilioWhatsAppProvider("", TOKEN, "whatsapp:+14155238886")
    with pytest.raises(ValueError, match="sender number"):
        TwilioWhatsAppProvider(SID, TOKEN, "")


# ------------------------------------------------------------------ webhook
def test_a_webhook_signature_computed_by_twilio_is_accepted_and_a_tampered_one_is_not():
    import base64
    import hashlib
    import hmac

    provider = TwilioWhatsAppProvider(SID, TOKEN, "whatsapp:+14155238886")
    url = "https://api.example.com/api/v1/inbound/whatsapp"
    body = b"Body=Hello&From=whatsapp%3A%2B201000000001&MessageSid=SM123&To=whatsapp%3A%2B14155238886"

    payload = url + "".join(
        f"{key}{value}" for key, value in sorted({
            "Body": "Hello", "From": "whatsapp:+201000000001", "MessageSid": "SM123",
            "To": "whatsapp:+14155238886"}.items())
    )
    signature = base64.b64encode(
        hmac.new(TOKEN.encode(), payload.encode(), hashlib.sha1).digest()).decode()

    assert provider.verify_webhook(body, signature, url) is True
    assert provider.verify_webhook(body, signature + "x", url) is False
    assert provider.verify_webhook(body, signature, url + "/other") is False
    assert provider.verify_webhook(body, signature) is False, "no URL means no verification"


def test_a_twilio_whatsapp_webhook_becomes_an_inbound_message():
    provider = TwilioWhatsAppProvider(SID, TOKEN, "whatsapp:+14155238886")
    messages = provider.parse_webhook({
        "MessageSid": "SM9f", "From": "whatsapp:+201000000001", "To": "whatsapp:+14155238886",
        "Body": "السعر 20500 جنيه للطن", "ProfileName": "Nile Steel", "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/ME1", "MediaContentType0": "application/pdf",
        "DateSent": "2026-10-31T08:00:00Z",
    })

    assert len(messages) == 1
    message = messages[0]
    assert message.channel == "whatsapp"
    assert message.external_id == "SM9f"
    assert message.sender == "+201000000001"
    assert message.body.startswith("السعر")
    assert message.attachments[0]["mime"] == "application/pdf"
    assert message.attachments[0]["id"].endswith("ME1")


def test_a_status_callback_maps_to_a_delivery_receipt():
    provider = TwilioWhatsAppProvider(SID, TOKEN, "whatsapp:+14155238886")

    statuses = provider.parse_statuses({"MessageSid": "SM9f", "MessageStatus": "read",
                                        "To": "whatsapp:+201000000001",
                                        "DateSent": "2026-10-31T08:00:00Z"})

    assert statuses == [{"provider_message_id": "SM9f", "status": "read",
                         "recipient": "+201000000001", "timestamp": "2026-10-31T08:00:00Z",
                         "error": ""}]


def test_sms_inbound_and_status_are_parsed_too():
    provider = TwilioSmsProvider(SID, TOKEN, "+14155238886")

    inbound = provider.parse_webhook({"MessageSid": "SM1", "From": "+201000000009",
                                      "Body": "stop", "SmsStatus": "received"})
    assert inbound[0].channel == "sms" and inbound[0].body == "stop"
    assert provider.parse_statuses({"MessageSid": "SM1", "SmsStatus": "delivered",
                                    "To": "+201000000009"})[0]["status"] == "delivered"


# ------------------------------------------------------------------ outbound voice
async def test_an_outbound_call_plays_the_disclosure_first_and_reports_the_real_sid(stub):
    from app.adapters.telephony_ocr_stt import TwilioTelephonyProvider

    base, recorder = stub
    provider = TwilioTelephonyProvider(SID, TOKEN, "+14155238886", base_url=base)

    result = await provider.place_call(to="+201000000001", script="نحتاج سعر الحديد",
                                       disclosure="هذه مكالمة آلية مسجلة", country="EG",
                                       language="ar-EG", consent_required=True)

    assert result.status == "queued" and result.provider_call_id == "CA1234"
    sent = recorder.calls[0]
    assert sent["From"] == "+14155238886" and sent["To"] == "+201000000001"
    assert sent["Twiml"].startswith("<Response><Say")
    assert "هذه مكالمة آلية مسجلة" in sent["Twiml"]
    assert sent["Twiml"].index("هذه مكالمة") < sent["Twiml"].index("نحتاج"), "disclosure comes first"


async def test_a_rejected_call_is_reported_as_failed_not_queued(stub):
    from app.adapters.telephony_ocr_stt import TwilioTelephonyProvider

    base, recorder = stub
    recorder.call_behaviour = [403]
    provider = TwilioTelephonyProvider(SID, TOKEN, "+14155238886", base_url=base, max_retries=0)

    result = await provider.place_call(to="+201", script="x", disclosure="y", country="EG",
                                       language="ar-EG", consent_required=True)

    assert result.status == "failed"
    assert "21215" in result.summary
    assert result.provider_call_id == ""


def test_a_voice_provider_without_credentials_refuses_at_construction():
    from app.adapters.telephony_ocr_stt import TwilioTelephonyProvider

    with pytest.raises(ValueError, match="TWILIO_ACCOUNT_SID"):
        TwilioTelephonyProvider("", TOKEN, "+14155238886")
    with pytest.raises(ValueError, match="sender number"):
        TwilioTelephonyProvider(SID, TOKEN, "")
