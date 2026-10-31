"""WhatsApp Business Cloud API adapter + deterministic Fake.

WhatsApp is the primary supplier channel in Egypt and Saudi Arabia, so the adapter does more than
send text: approved templates, documents, interactive quote requests (WhatsApp Flows when a flow id is
configured), delivery/read receipts parsed from the webhook, and inbound media.

Everything is deterministic in the Fake so the whole pipeline — send → reply → extraction → inbox —
runs in CI without a Meta account. The real adapter maps the same contract onto the Graph API.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from app.adapters.base import InboundMessage, SendResult, fake_id

MEDIA_TYPES = {"image", "document", "audio", "video", "sticker"}


class _ParsingMixin:
    """Webhook parsing shared by the Fake and the Cloud adapter (same payload shape)."""

    def parse_webhook(self, payload: dict[str, Any]) -> list[InboundMessage]:
        messages: list[InboundMessage] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                contacts = {c.get("wa_id", ""): c.get("profile", {}).get("name", "")
                            for c in value.get("contacts", [])}
                for msg in value.get("messages", []):
                    kind = msg.get("type", "text")
                    body = ""
                    attachments: list[dict[str, Any]] = []
                    if kind == "text":
                        body = msg.get("text", {}).get("body", "")
                    elif kind == "audio":
                        audio = msg.get("audio", {})
                        body = "[voice note]"
                        attachments.append({"kind": "audio", "id": audio.get("id", ""),
                                            "mime": audio.get("mime_type", "audio/ogg"),
                                            "filename": audio.get("filename", "voice-note.ogg")})
                    elif kind in MEDIA_TYPES:
                        media = msg.get(kind, {})
                        filename = media.get("filename") or f"{kind}-{msg.get('id', '')[:8]}"
                        body = f"[{kind}] {filename}"
                        attachments.append({"kind": kind, "id": media.get("id", ""),
                                            "mime": media.get("mime_type", ""), "filename": filename,
                                            "caption": media.get("caption", "")})
                    elif kind == "button":
                        body = msg.get("button", {}).get("text", "")
                    elif kind == "interactive":
                        interactive = msg.get("interactive", {})
                        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                        body = reply.get("title", "") or json.dumps(interactive)[:200]
                    else:
                        body = json.dumps(msg.get(kind, {}))[:400]
                    sender = msg.get("from", "")
                    messages.append(InboundMessage(
                        channel="whatsapp", external_id=msg.get("id", ""), sender=sender,
                        subject="", body=body, attachments=attachments, raw=msg,
                        received_at=str(msg.get("timestamp", "")),
                    ))
                    messages[-1].raw["profile_name"] = contacts.get(sender, "")  # type: ignore[index]
        return messages

    def parse_statuses(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Delivery / read / failed receipts. Separate from messages: they are not conversations."""
        out: list[dict[str, Any]] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for status in value.get("statuses", []):
                    out.append({
                        "provider_message_id": status.get("id", ""),
                        "status": status.get("status", ""),
                        "recipient": status.get("recipient_id", ""),
                        "timestamp": status.get("timestamp", ""),
                        "error": (status.get("errors") or [{}])[0].get("title", ""),
                    })
        return out

    def parse_consent_events(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Opt-in/opt-out events WhatsApp emits for its own consent flows (if any)."""
        out: list[dict[str, Any]] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for event in value.get("opt_in_events", []) or []:
                    out.append({"wa_id": event.get("wa_id", ""), "kind": "opt_in",
                                "channel": "whatsapp", "type": event.get("type", "")})
        return out


class FakeWhatsAppProvider(_ParsingMixin):
    name = "fake"
    supports_templates = True
    supports_documents = True
    supports_read_receipts = True
    supports_interactive = True

    def __init__(self, verify_token: str = "verify-me") -> None:
        self.verify_token = verify_token
        self.outbox: list[dict[str, Any]] = []
        self.inbox: list[InboundMessage] = []
        self.media: dict[str, bytes] = {}

    # ---------------------------------------------------------------- outbound
    async def send_text(self, *, to: str, body: str, template: str = "") -> SendResult:
        message_id = fake_id("wa", to, body[:40], str(len(self.outbox)))
        self.outbox.append({"to": to, "type": "text", "body": body, "template": template,
                            "message_id": message_id})
        return SendResult(ok=True, provider_message_id=message_id)

    async def send_template(self, *, to: str, template: str, variables: list[str]) -> SendResult:
        message_id = fake_id("wat", to, template, ",".join(variables))
        self.outbox.append({"to": to, "type": "template", "template": template,
                            "variables": variables, "message_id": message_id})
        return SendResult(ok=True, provider_message_id=message_id)

    async def send_document(self, *, to: str, filename: str, data: bytes, caption: str = "",
                            mime: str = "") -> SendResult:
        message_id = fake_id("wad", to, filename, str(len(self.outbox)))
        self.outbox.append({"to": to, "type": "document", "filename": filename, "mime": mime,
                            "caption": caption, "size": len(data), "message_id": message_id})
        return SendResult(ok=True, provider_message_id=message_id)

    async def send_interactive(self, *, to: str, body: str, options: list[str],
                               flow: str = "") -> SendResult:
        message_id = fake_id("wai", to, body[:40], flow, str(len(self.outbox)))
        self.outbox.append({"to": to, "type": "interactive", "body": body, "options": options,
                            "flow": flow, "message_id": message_id})
        return SendResult(ok=True, provider_message_id=message_id)

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        return self.media.get(media_id, b""), ""

    # ---------------------------------------------------------------- webhook
    def verify_webhook(self, body: bytes, signature: str) -> bool:
        expected = hmac.new(self.verify_token.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    # ---------------------------------------------------------------- test helpers
    def queue_inbound(self, *, sender: str, body: str, kind: str = "text") -> InboundMessage:
        msg = InboundMessage(channel="whatsapp", external_id=fake_id("wain", sender, body[:32]),
                             sender=sender, subject="", body=body, raw={"type": kind})
        self.inbox.append(msg)
        return msg

    def queue_media(self, *, sender: str, filename: str, data: bytes, mime: str = "application/pdf",
                    caption: str = "") -> InboundMessage:
        """Queue a document *with* its bytes, as a real webhook + media download would deliver it."""
        media_id = fake_id("media", sender, filename)
        self.media[media_id] = data
        msg = InboundMessage(
            channel="whatsapp", external_id=fake_id("wain", sender, filename), sender=sender,
            subject="", body=caption or f"[document] {filename}",
            attachments=[{"kind": "document", "id": media_id, "filename": filename, "mime": mime,
                          "caption": caption}],
            raw={"type": "document"},
        )
        self.inbox.append(msg)
        return msg

    def last_outbound(self) -> dict[str, Any]:
        return self.outbox[-1] if self.outbox else {}

    async def poll_inbox(self, limit: int = 25) -> list[InboundMessage]:
        items, self.inbox = self.inbox[:limit], self.inbox[limit:]
        return items

    def clear(self) -> None:
        self.outbox.clear()
        self.inbox.clear()
        self.media.clear()


class CloudWhatsAppProvider(_ParsingMixin):
    """Meta WhatsApp Business Cloud API (approved templates, 24h session window)."""

    name = "cloud_api"
    supports_templates = True
    supports_documents = True
    supports_read_receipts = True
    supports_interactive = True
    BASE = "https://graph.facebook.com/v20.0"

    def __init__(self, token: str, phone_id: str, app_secret: str = "",
                 quote_flow_id: str = "", base_url: str = "") -> None:
        self.token = token
        self.phone_id = phone_id
        self.app_secret = app_secret
        self.quote_flow_id = quote_flow_id
        self.base_url = (base_url or self.BASE).rstrip("/")

    async def _post(self, payload: dict[str, Any]) -> SendResult:
        url = f"{self.base_url}/{self.phone_id}/messages"
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(url, headers=headers, json=payload)
            data = resp.json()
            if resp.status_code >= 400:
                return SendResult(ok=False, error=json.dumps(data)[:300], raw=data)
            return SendResult(ok=True,
                              provider_message_id=data.get("messages", [{}])[0].get("id", ""),
                              raw=data)
        except Exception as exc:  # pragma: no cover - network dependent
            return SendResult(ok=False, error=str(exc)[:300])

    async def send_text(self, *, to: str, body: str, template: str = "") -> SendResult:
        return await self._post({"messaging_product": "whatsapp", "to": to,
                                 "type": "text", "text": {"body": body}})

    async def send_template(self, *, to: str, template: str, variables: list[str]) -> SendResult:
        return await self._post({
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {
                "name": template, "language": {"code": "ar"},
                "components": [{"type": "body", "parameters": [
                    {"type": "text", "text": v} for v in variables
                ]}],
            },
        })

    async def _upload(self, filename: str, data: bytes, mime: str) -> str:  # pragma: no cover - network
        url = f"{self.BASE}/{self.phone_id}/media"
        files = {"file": (filename, data, mime or "application/octet-stream")}
        data_fields = {"messaging_product": "whatsapp", "type": mime or "application/octet-stream"}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers={"Authorization": f"Bearer {self.token}"},
                                     data=data_fields, files=files)
        return resp.json().get("id", "")

    async def send_document(self, *, to: str, filename: str, data: bytes, caption: str = "",
                            mime: str = "") -> SendResult:
        media_id = await self._upload(filename, data, mime)  # pragma: no cover - network
        return await self._post({
            "messaging_product": "whatsapp", "to": to, "type": "document",
            "document": {"id": media_id, "filename": filename, "caption": caption[:1000]},
        })

    async def send_interactive(self, *, to: str, body: str, options: list[str],
                               flow: str = "") -> SendResult:
        if flow and self.quote_flow_id:
            return await self._post({
                "messaging_product": "whatsapp", "to": to, "type": "interactive",
                "interactive": {
                    "type": "flow", "body": {"text": body[:1000]},
                    "action": {"name": "flow", "parameters": {
                        "flow_message_version": "3", "flow_id": self.quote_flow_id,
                        "flow_token": flow, "flow_cta": "Open",
                    }},
                },
            })
        return await self._post({
            "messaging_product": "whatsapp", "to": to, "type": "interactive",
            "interactive": {
                "type": "button", "body": {"text": body[:1000]},
                "action": {"buttons": [
                    {"type": "reply", "reply": {"id": f"opt_{i}", "title": option[:20]}}
                    for i, option in enumerate(options[:3])
                ]},
            },
        })

    async def download_media(self, media_id: str) -> tuple[bytes, str]:  # pragma: no cover - network
        async with httpx.AsyncClient(timeout=30) as client:
            meta = await client.get(f"{self.BASE}/{media_id}",
                                    headers={"Authorization": f"Bearer {self.token}"})
            url = meta.json().get("url", "")
            if not url:
                return b"", ""
            data = await client.get(url, headers={"Authorization": f"Bearer {self.token}"})
        return data.content, data.headers.get("content-type", "")

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        if not self.app_secret:
            return False
        expected = "sha256=" + hmac.new(self.app_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")
