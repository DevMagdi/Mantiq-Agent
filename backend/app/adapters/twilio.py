"""Twilio messaging transport: WhatsApp and SMS over the same account.

Twilio is a first-class option next to Meta's Cloud API because it is what most Egyptian and Gulf
enterprises already have a contract with, and it covers voice, WhatsApp and SMS on one pair of
credentials. This module implements the *real* Programmable Messaging API; nothing here is a stub.

Design notes that matter in production:

* **Base URL is injectable.** It defaults to `https://api.twilio.com`, and the tests point it at a
  local HTTP server so the exact request shape (basic auth, form fields, 429/5xx handling) is
  exercised without pretending to talk to Twilio.
* **Retries are bounded and only for retryable failures.** 429 and 5xx are retried with exponential
  backoff; a 4xx (bad number, unapproved template, insufficient funds) is returned immediately with
  Twilio's error code, because retrying it just burns money.
* **Errors are values, not exceptions.** `SendResult(ok=False, error=...)` keeps the caller's
  bookkeeping (message status, channel events) intact and lets the UI show what happened.
* **Signatures are verified with Twilio's scheme** (HMAC-SHA1 of the URL plus sorted POST params,
  base64) when `TWILIO_AUTH_TOKEN` is present. An unsigned webhook is refused.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote_plus

import httpx

from app.adapters.base import InboundMessage, SendResult

log = logging.getLogger("procureai.adapters.twilio")

DEFAULT_BASE = "https://api.twilio.com"
API_VERSION = "2010-04-01"

#: Statuses Twilio answers with when the request is worth repeating. Everything else is final.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass
class TwilioResponse:
    """What Twilio actually answered, whether or not it was a success."""

    status_code: int = 0
    body: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status_code and self.status_code < 400

    @property
    def message(self) -> str:
        """Twilio's human message + numeric code, or the transport error."""
        if self.error:
            return self.error
        detail = self.body.get("message") or self.body.get("error_message") or ""
        code = self.body.get("code", "")
        return f"{detail} (code {code})".strip() if code else str(detail)


async def post_form(url: str, *, sid: str, token: str, data: dict[str, str],
                    timeout: float = 20.0, max_retries: int = 2) -> TwilioResponse:
    """POST an `x-www-form-urlencoded` request, retrying only what Twilio says is retryable.

    Shared by messaging and voice so one retry policy governs every Twilio call. A 4xx is returned
    immediately: a bad number, an unapproved template or a missing balance will stay that way.
    """
    last = TwilioResponse()
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, auth=(sid, token)) as client:
                response = await client.post(url, data=data)
        except Exception as exc:  # noqa: BLE001 - a network failure is a value, not a crash
            last = TwilioResponse(error=f"{type(exc).__name__}: {exc}")
        else:
            try:
                body = response.json()
            except ValueError:
                body = {"raw": response.text[:300]}
            last = TwilioResponse(status_code=response.status_code,
                                  body=body if isinstance(body, dict) else {"raw": body})
            if last.ok or response.status_code not in RETRY_STATUSES:
                return last
            log.warning("twilio %s answered %s; retrying", url, response.status_code)
        if attempt < max_retries:
            await asyncio.sleep(0.5 * (2 ** attempt))
    return last


def normalise_whatsapp(number: str) -> str:
    """`+201000000000`, `whatsapp:+201000000000` and `whatsapp:201000000000` all mean one thing."""
    value = (number or "").strip()
    if value.lower().startswith("whatsapp:"):
        value = value.split(":", 1)[1]
    value = value.replace(" ", "").replace("-", "")
    if value and not value.startswith("+"):
        value = f"+{value}"
    return value


def whatsapp_address(number: str) -> str:
    return f"whatsapp:{normalise_whatsapp(number)}"


class _TwilioMessaging:
    """Shared transport for WhatsApp and SMS (identical API, different address prefix)."""

    channel = "sms"

    def __init__(self, account_sid: str, auth_token: str, from_number: str, *,
                 base_url: str = DEFAULT_BASE, timeout: float = 20.0, max_retries: int = 2) -> None:
        if not account_sid or not auth_token:
            raise ValueError("Twilio needs TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN")
        if not from_number:
            raise ValueError("Twilio needs a sender number (TWILIO_WHATSAPP_FROM / TWILIO_SMS_FROM)")
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    # ------------------------------------------------------------------ transport
    @property
    def _messages_url(self) -> str:
        return f"{self.base_url}/{API_VERSION}/Accounts/{self.account_sid}/Messages.json"

    def _address(self, number: str) -> str:
        return whatsapp_address(number) if self.channel == "whatsapp" else normalise_whatsapp(number)

    async def _post(self, form: dict[str, str]) -> SendResult:
        result = await post_form(self._messages_url, sid=self.account_sid, token=self.auth_token,
                                 data=form, timeout=self.timeout, max_retries=self.max_retries)
        if result.ok:
            return SendResult(ok=True, provider_message_id=str(result.body.get("sid", "")),
                              raw=result.body)
        error = result.message if not result.status_code else f"HTTP {result.status_code}: {result.message}"
        return SendResult(ok=False, error=error[:300], raw=result.body)

    # ------------------------------------------------------------------ send
    async def send_text(self, *, to: str, body: str, template: str = "") -> SendResult:
        return await self._post({"To": self._address(to), "From": self.from_number,
                                 "Body": body})

    async def send_document(self, *, to: str, filename: str, data: bytes, caption: str = "",
                            mime: str = "") -> SendResult:
        """Twilio fetches media from a URL; an inline upload is served by the caller's storage.

        Rather than silently dropping the document, a caller that has no public URL for the file gets
        an explicit refusal, so the audit trail says "not sent" instead of "sent" without the file.
        """
        return SendResult(ok=False,
                          error="Twilio requires a public MediaUrl; use the storage adapter to publish "
                                "the file and call send_document_url()",
                          raw={"filename": filename, "bytes": len(data), "mime": mime, "caption": caption})

    async def send_document_url(self, *, to: str, media_url: str, caption: str = "") -> SendResult:
        form = {"To": self._address(to), "From": self.from_number, "MediaUrl": media_url}
        if caption:
            form["Body"] = caption[:1500]
        return await self._post(form)

    async def send_interactive(self, *, to: str, body: str, options: list[str],
                               flow: str = "") -> SendResult:
        """WhatsApp quick-reply buttons exist on Twilio as Content templates.

        Without a published Content template the honest fallback is a numbered text message — the
        supplier can still answer with a number, and the extraction pipeline reads it as usual.
        """
        numbered = body + "\n\n" + "\n".join(f"{index + 1}. {option}"
                                             for index, option in enumerate(options))
        return await self.send_text(to=to, body=f"{numbered}\n\nReply with the number.")


async def fetch_account(sid: str, token: str, base_url: str = DEFAULT_BASE) -> TwilioResponse:
    """`GET /Accounts/{sid}` — the cheapest honest proof that a sid/token pair works."""
    url = f"{(base_url or DEFAULT_BASE).rstrip('/')}/2010-04-01/Accounts/{sid}.json"
    try:
        async with httpx.AsyncClient(timeout=15,
                                     auth=httpx.BasicAuth(sid, token)) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        return TwilioResponse(error=f"{type(exc).__name__}: {exc}"[:300])
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return TwilioResponse(status_code=resp.status_code, body=body)


class TwilioWhatsAppProvider(_TwilioMessaging):
    """WhatsApp over Twilio Programmable Messaging (real API, no fake fallback)."""

    name = "twilio_whatsapp"
    channel = "whatsapp"
    supports_templates = True
    supports_documents = True
    supports_read_receipts = True
    supports_interactive = False  # quick replies need a published Content template

    async def send_template(self, *, to: str, template: str, variables: list[str]) -> SendResult:
        """A Twilio Content template: `template` is the ContentSid, `variables` the numbered slots."""
        form = {
            "To": self._address(to),
            "From": self.from_number,
            "ContentSid": template,
            "ContentVariables": json.dumps({str(i + 1): value for i, value in enumerate(variables)}),
        }
        return await self._post(form)

    # ------------------------------------------------------------------ webhook
    def verify_webhook(self, body: bytes, signature: str, url: str = "") -> bool:
        """Twilio signs `url + sorted(params)` with HMAC-SHA1 keyed by the auth token."""
        if not signature:
            return False
        if not url:
            # Without the request URL the signature cannot be recomputed; refuse rather than guess.
            return False
        try:
            params: dict[str, str] = {}
            for pair in body.decode().split("&"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    params[key] = value
        except Exception:  # noqa: BLE001
            return False
        payload = url + "".join(f"{key}{_unquote(params[key])}" for key in sorted(params))
        digest = hmac.new(self.auth_token.encode(), payload.encode(), hashlib.sha1).digest()
        expected = base64.b64encode(digest).decode()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, payload: dict) -> list[InboundMessage]:
        """A Twilio WhatsApp webhook is form-encoded; the router converts it to this shape."""
        if "MessageSid" not in payload and "SmsMessageSid" not in payload:
            return []
        sender = normalise_whatsapp(payload.get("From", ""))
        attachments: list[dict] = []
        for index in range(int(payload.get("NumMedia") or 0)):
            attachments.append({
                "kind": (payload.get(f"MediaContentType{index}") or "application/octet-stream")
                .split("/", 1)[0],
                "id": payload.get(f"MediaUrl{index}", ""),
                "mime": payload.get(f"MediaContentType{index}", ""),
                "filename": payload.get(f"MediaUrl{index}", "").rsplit("/", 1)[-1],
            })
        return [InboundMessage(
            channel="whatsapp",
            external_id=payload.get("MessageSid") or payload.get("SmsMessageSid", ""),
            sender=sender,
            subject=payload.get("ProfileName", ""),
            body=payload.get("Body", ""),
            attachments=attachments,
            received_at=payload.get("DateSent", ""),
            raw=dict(payload),
        )]

    def parse_statuses(self, payload: dict) -> list[dict]:
        """Twilio posts one status callback per transition (`MessageStatus`)."""
        status = payload.get("MessageStatus") or payload.get("SmsStatus")
        if not status:
            return []
        return [{
            "provider_message_id": payload.get("MessageSid") or payload.get("SmsMessageSid", ""),
            "status": status,
            "recipient": normalise_whatsapp(payload.get("To", "")),
            "timestamp": payload.get("DateSent", ""),
            "error": payload.get("ErrorMessage", "") or payload.get("ErrorCode", ""),
        }]

    def parse_consent_events(self, payload: dict) -> list[dict]:
        return []

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        if not media_id:
            return b"", ""
        async with httpx.AsyncClient(timeout=30, auth=(self.account_sid, self.auth_token),
                                     follow_redirects=True) as client:
            response = await client.get(media_id)
        return response.content, response.headers.get("content-type", "")


class TwilioSmsProvider(_TwilioMessaging):
    """SMS over Twilio — the fallback when a supplier has a phone but no WhatsApp."""

    name = "twilio_sms"
    channel = "sms"
    supports_templates = False
    supports_documents = False
    supports_read_receipts = True
    supports_interactive = False

    async def send_template(self, *, to: str, template: str, variables: list[str]) -> SendResult:
        # SMS has no template registry; the caller renders the bilingual text and sends it as text.
        return SendResult(ok=False, error="SMS does not support templates; send rendered text instead")

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        return b"", ""

    def verify_webhook(self, body: bytes, signature: str, url: str = "") -> bool:
        return False

    def parse_webhook(self, payload: dict) -> list[InboundMessage]:
        sender = normalise_whatsapp(payload.get("From", ""))
        if not payload.get("Body"):
            return []
        return [InboundMessage(channel="sms",
                               external_id=payload.get("MessageSid") or payload.get("SmsMessageSid", ""),
                               sender=sender, subject="", body=payload.get("Body", ""),
                               received_at=payload.get("DateSent", ""), raw=dict(payload))]

    def parse_statuses(self, payload: dict) -> list[dict]:
        status = payload.get("SmsStatus") or payload.get("MessageStatus")
        if not status:
            return []
        return [{"provider_message_id": payload.get("MessageSid", ""), "status": status,
                 "recipient": normalise_whatsapp(payload.get("To", "")),
                 "timestamp": payload.get("DateSent", ""), "error": payload.get("ErrorMessage", "")}]


class FakeSmsProvider:
    """Deterministic SMS transport for tests only — never selected in a real deployment."""

    name = "fake"
    channel = "sms"
    supports_templates = False
    supports_documents = False
    supports_read_receipts = True
    supports_interactive = False

    def __init__(self) -> None:
        self.outbox: list[dict] = []

    async def send_text(self, *, to: str, body: str, template: str = "") -> SendResult:
        self.outbox.append({"to": to, "body": body})
        return SendResult(ok=True, provider_message_id=f"SM{len(self.outbox):06d}")

    async def send_template(self, *, to: str, template: str, variables: list[str]) -> SendResult:
        return await self.send_text(to=to, body=f"{template}: {', '.join(variables)}")

    async def send_document(self, *, to: str, filename: str, data: bytes, caption: str = "",
                            mime: str = "") -> SendResult:
        return SendResult(ok=False, error="SMS cannot carry documents")

    async def send_interactive(self, *, to: str, body: str, options: list[str],
                               flow: str = "") -> SendResult:
        return await self.send_text(to=to, body=body)

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        return b"", ""

    def verify_webhook(self, body: bytes, signature: str, url: str = "") -> bool:
        return True

    def parse_webhook(self, payload: dict) -> list[InboundMessage]:
        return []

    def parse_statuses(self, payload: dict) -> list[dict]:
        return []


def _unquote(value: str) -> str:
    return unquote_plus(value)


__all__ = [
    "DEFAULT_BASE",
    "FakeSmsProvider",
    "TwilioSmsProvider",
    "TwilioWhatsAppProvider",
    "normalise_whatsapp",
    "whatsapp_address",
]
