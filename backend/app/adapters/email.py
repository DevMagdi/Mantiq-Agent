"""Email adapters: deterministic Fake (dev/tests) and real SMTP/IMAP."""
from __future__ import annotations

import asyncio
import email
import imaplib
import smtplib
from email.message import EmailMessage
from typing import Any

from app.adapters.base import InboundMessage, SendResult, fake_id


class FakeEmailProvider:
    """In-memory outbox/inbox used by tests, demos and offline deployments."""

    name = "fake"

    def __init__(self) -> None:
        self.outbox: list[dict[str, Any]] = []
        self.inbox: list[InboundMessage] = []

    async def send(self, *, to: str, subject: str, body: str, html: str = "",
                   attachments: list[dict[str, Any]] | None = None,
                   reply_to: str = "") -> SendResult:
        message_id = fake_id("email", to, subject, str(len(self.outbox)))
        self.outbox.append({
            "to": to, "subject": subject, "body": body, "html": html,
            "attachments": attachments or [], "reply_to": reply_to, "message_id": message_id,
        })
        return SendResult(ok=True, provider_message_id=message_id)

    async def poll_inbox(self, limit: int = 25) -> list[InboundMessage]:
        items, self.inbox = self.inbox[:limit], self.inbox[limit:]
        return items

    # --- test helper -----------------------------------------------------
    def queue_inbound(self, *, sender: str, subject: str, body: str,
                      attachments: list[dict[str, Any]] | None = None, in_reply_to: str = "") -> InboundMessage:
        msg = InboundMessage(
            channel="email", external_id=fake_id("in", sender, subject, body[:32]), sender=sender,
            subject=subject, body=body, attachments=attachments or [], in_reply_to=in_reply_to,
            received_at="",
        )
        self.inbox.append(msg)
        return msg

    def clear(self) -> None:
        self.outbox.clear()
        self.inbox.clear()


class SmtpEmailProvider:
    """Real SMTP send + IMAP poll (Microsoft 365 / Gmail / any provider)."""

    name = "smtp"

    def __init__(self, host: str, port: int, user: str, password: str, sender: str,
                 imap_host: str | None = None) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender
        self.imap_host = imap_host or host

    def _send_sync(self, to: str, subject: str, body: str, html: str, reply_to: str) -> SendResult:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype="html")
        try:
            with smtplib.SMTP(self.host, self.port, timeout=20) as server:
                server.ehlo()
                if self.port != 25:
                    try:
                        server.starttls()
                    except smtplib.SMTPNotSupportedError:
                        pass
                if self.user:
                    server.login(self.user, self.password)
                server.send_message(msg)
            return SendResult(ok=True, provider_message_id=msg.get("Message-ID", fake_id("smtp", to, subject)))
        except Exception as exc:  # pragma: no cover - network dependent
            return SendResult(ok=False, error=str(exc)[:300])

    async def send(self, *, to: str, subject: str, body: str, html: str = "",
                   attachments: list[dict[str, Any]] | None = None,
                   reply_to: str = "") -> SendResult:
        return await asyncio.to_thread(self._send_sync, to, subject, body, html, reply_to)

    def _poll_sync(self, limit: int) -> list[InboundMessage]:
        out: list[InboundMessage] = []
        try:
            with imaplib.IMAP4_SSL(self.imap_host) as imap:
                imap.login(self.user, self.password)
                imap.select("INBOX")
                _typ, data = imap.search(None, "UNSEEN")
                ids = (data[0] or b"").split()[-limit:]
                for msg_id in ids:
                    _typ, payload = imap.fetch(msg_id, "(RFC822)")
                    if not payload or not isinstance(payload[0], tuple):
                        continue
                    parsed = email.message_from_bytes(payload[0][1])
                    body = ""
                    attachments: list[dict[str, Any]] = []
                    for part in parsed.walk():
                        ctype = part.get_content_type()
                        if ctype == "text/plain" and not part.get_filename():
                            body += part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                        elif part.get_filename():
                            attachments.append({
                                "filename": part.get_filename(),
                                "content": part.get_payload(decode=True),
                                "mime": ctype,
                            })
                    out.append(InboundMessage(
                        channel="email", external_id=parsed.get("Message-ID", str(msg_id)),
                        sender=parsed.get("From", ""), subject=parsed.get("Subject", ""), body=body,
                        attachments=attachments, in_reply_to=parsed.get("In-Reply-To", ""),
                    ))
                    imap.store(msg_id, "+FLAGS", "\\Seen")
        except Exception:  # pragma: no cover - network dependent
            return out
        return out

    async def poll_inbox(self, limit: int = 25) -> list[InboundMessage]:
        return await asyncio.to_thread(self._poll_sync, limit)
