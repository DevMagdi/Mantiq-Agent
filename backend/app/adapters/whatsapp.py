"""WhatsApp Business Cloud API adapter + deterministic Fake."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from app.adapters.base import InboundMessage, SendResult, fake_id


class FakeWhatsAppProvider:
    name = "fake"

    def __init__(self, verify_token: str = "verify-me") -> None:
        self.verify_token = verify_token
        self.outbox: list[dict[str, Any]] = []
        self.inbox: list[InboundMessage] = []

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

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        expected = hmac.new(self.verify_token.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    def parse_webhook(self, payload: dict[str, Any]) -> list[InboundMessage]:
        messages: list[InboundMessage] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    body = ""
                    if msg.get("type") == "text":
                        body = msg.get("text", {}).get("body", "")
                    elif msg.get("type") == "audio":
                        body = "[voice note]"
                    elif msg.get("type") == "document":
                        body = f"[document] {msg.get('document', {}).get('filename', '')}"
                    messages.append(InboundMessage(
                        channel="whatsapp", external_id=msg.get("id", ""),
                        sender=msg.get("from", ""), subject="", body=body,
                        attachments=[msg.get("document", {})] if msg.get("type") == "document" else [],
                        raw=msg,
                    ))
        return messages

    def queue_inbound(self, *, sender: str, body: str, kind: str = "text") -> InboundMessage:
        msg = InboundMessage(channel="whatsapp", external_id=fake_id("wain", sender, body[:32]),
                             sender=sender, subject="", body=body, raw={"type": kind})
        self.inbox.append(msg)
        return msg

    async def poll_inbox(self, limit: int = 25) -> list[InboundMessage]:
        items, self.inbox = self.inbox[:limit], self.inbox[limit:]
        return items

    def clear(self) -> None:
        self.outbox.clear()
        self.inbox.clear()


class CloudWhatsAppProvider:
    """Meta WhatsApp Business Cloud API (approved templates, 24h session window)."""

    name = "cloud_api"
    BASE = "https://graph.facebook.com/v20.0"

    def __init__(self, token: str, phone_id: str, app_secret: str = "") -> None:
        self.token = token
        self.phone_id = phone_id
        self.app_secret = app_secret

    async def _post(self, payload: dict[str, Any]) -> SendResult:
        url = f"{self.BASE}/{self.phone_id}/messages"
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(url, headers=headers, json=payload)
            data = resp.json()
            if resp.status_code >= 400:
                return SendResult(ok=False, error=json.dumps(data)[:300], raw=data)
            return SendResult(ok=True, provider_message_id=data.get("messages", [{}])[0].get("id", ""), raw=data)
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

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        if not self.app_secret:
            return False
        expected = "sha256=" + hmac.new(self.app_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    def parse_webhook(self, payload: dict[str, Any]) -> list[InboundMessage]:
        return FakeWhatsAppProvider().parse_webhook(payload)
