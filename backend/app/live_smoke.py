"""`procureai live-smoke` — one real call against every configured vendor, on demand.

This is the operator's proof that the deployment can reach the outside world, run **after** `doctor`
and **before** a go-live. It is deliberately *not* part of the test suite: it sends real messages,
places a real call and writes a real object, so it must be invoked explicitly with `--confirm`.

Guarantees

* **No credential, no call.** A provider whose own adapter is still on a fake is reported as
  ``FAIL`` in a strict deployment (it means the deployment is not ready) and as ``SKIP`` elsewhere.
* **The LLM check is real end-to-end**: one completion against the configured model, asserting the
  response shape *and* that the vendor metered tokens (``tokens_in > 0``) — the same numbers the
  billing pipeline records.
* **The email check actually delivers.** If ``LIVE_SMOKE_IMAP_HOST``/``LIVE_SMOKE_IMAP_USER``/
  ``LIVE_SMOKE_IMAP_PASSWORD`` are set, the probe polls the mailbox for the unique subject line it
  sent and reports ``receipt_verified`` — otherwise the send is reported as PASS with an explicit
  ``receipt not verified`` note. Nothing is ever marked "verified" without the message being seen.
* **The telephony check places a real call** to ``--call-to`` with the disclosure-first script and
  then polls the call resource until it reaches a terminal state (or the timeout). A ``no-answer``
  is an honest PASS with the status reported — the smoke proves placement, not the callee picking up.
* **The storage check round-trips a real object**: PUT → HEAD/GET → presigned GET fetched over HTTP →
  DELETE. The key is removed afterwards; a presigned URL that answers 4xx fails the check.
* **No secret is printed.** Variables are reported by name and set/unset, never by value.

Exit codes: ``0`` every selected check passed, ``1`` at least one check failed, ``2`` bad usage.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings
from app.core.providers import FAKE_PROVIDERS

ALL_CHECKS = ("llm", "email", "whatsapp", "telephony", "storage", "search")


@dataclass
class Check:
    name: str
    status: str          # PASS | FAIL | SKIP
    detail: str = ""
    facts: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.name, "status": self.status, "detail": self.detail, **self.facts}


def _is_fake(value: str | None) -> bool:
    return (value or "fake").strip().lower() in FAKE_PROVIDERS


def _strict_fail(name: str, missing: str) -> Check | None:
    """In a strict deployment a faked/unconfigured critical vendor is a failure, not a skip."""
    if settings.strict_providers:
        return Check(name, "FAIL",
                     f"{missing} — APP_ENV={settings.app_env} requires the real vendor; "
                     "set the variables or ALLOW_FAKE_PROVIDERS=true for a deliberately fake box")
    return None


# ------------------------------------------------------------------ LLM
async def check_llm() -> Check:
    provider = (settings.llm_provider or "fake").lower()
    if _is_fake(provider):
        strict = _strict_fail("llm", "LLM_PROVIDER is a fake")
        return strict or Check("llm", "SKIP", "LLM_PROVIDER=fake (set LLM_PROVIDER=anthropic|openai_compatible)")

    from app.agents.llm import default_provider
    from app.agents.llm.base import LLMMessage, LLMRequest

    try:
        client = default_provider()
    except Exception as exc:  # noqa: BLE001 - report, never traceback the operator
        return Check("llm", "FAIL", f"provider refused to build: {exc}")

    request = LLMRequest(
        task="live_smoke",
        messages=[LLMMessage(role="user",
                             content="Reply with exactly the word: ok. Do not add punctuation.")],
        max_tokens=16,
        temperature=0.0,
    )
    started = time.perf_counter()
    try:
        response = await client.complete(request)
    except Exception as exc:  # noqa: BLE001
        return Check("llm", "FAIL", f"completion failed: {type(exc).__name__}: {exc}"[:300])
    latency_ms = int((time.perf_counter() - started) * 1000)

    text = (response.text or "").strip()
    if not text and not response.tool_calls:
        return Check("llm", "FAIL", "the vendor answered with an empty completion")
    if response.tokens_in <= 0:
        return Check("llm", "FAIL",
                     "the vendor returned a completion but no metering (usage.input_tokens=0); "
                     "the billing pipeline depends on this")
    return Check("llm", "PASS",
                 f"{response.provider}:{response.model} answered in {latency_ms} ms",
                 {"tokens_in": response.tokens_in, "tokens_out": response.tokens_out,
                  "model": response.model, "provider": response.provider,
                  "latency_ms": latency_ms})


# ------------------------------------------------------------------ email
def _imap_verify(subject: str, *, timeout_s: int) -> tuple[bool, str]:
    """Poll the configured IMAP mailbox for the smoke message. Never marks it verified without seeing it."""
    import email
    import imaplib

    host = os.environ.get("LIVE_SMOKE_IMAP_HOST", "")
    user = os.environ.get("LIVE_SMOKE_IMAP_USER", "")
    password = os.environ.get("LIVE_SMOKE_IMAP_PASSWORD", "")
    if not (host and user and password):
        return False, "receipt not verified — set LIVE_SMOKE_IMAP_HOST/USER/PASSWORD to verify via IMAP"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with imaplib.IMAP4_SSL(host) as imap:
                imap.login(user, password)
                imap.select("INBOX")
                _typ, data = imap.search(None, "ALL")
                for msg_id in (data[0] or b"").split()[-10:]:
                    _typ, payload = imap.fetch(msg_id, "(BODY[HEADER.FIELDS (SUBJECT)])")
                    if not payload or not isinstance(payload[0], tuple):
                        continue
                    parsed = email.message_from_bytes(payload[0][1])
                    if subject in (parsed.get("Subject") or ""):
                        return True, f"receipt verified in INBOX of {user}"
        except Exception as exc:  # noqa: BLE001 - report, never pretend
            return False, f"IMAP check failed: {type(exc).__name__}: {exc}"[:200]
        time.sleep(2)
    return False, f"not seen in INBOX within {timeout_s}s (it may still arrive; re-check the mailbox)"


async def check_email(email_to: str, *, timeout_s: int) -> Check:
    provider = (settings.email_provider or "fake").lower()
    if _is_fake(provider):
        strict = _strict_fail("email", "EMAIL_PROVIDER is a fake")
        return strict or Check("email", "SKIP", "EMAIL_PROVIDER=fake (set EMAIL_PROVIDER=smtp)")
    if provider != "smtp":
        return Check("email", "FAIL", f"EMAIL_PROVIDER={provider!r} is not a live transport this tool knows")
    if not email_to:
        return Check("email", "SKIP" if not settings.strict_providers else "FAIL",
                     "no recipient — pass --email-to or set LIVE_SMOKE_EMAIL_TO to a real mailbox you control")

    from app.adapters.email import SmtpEmailProvider

    client = SmtpEmailProvider(settings.smtp_host, settings.smtp_port, settings.smtp_user,
                               settings.smtp_password, settings.smtp_from)
    marker = uuid.uuid4().hex[:12]
    subject = f"ProcureAI live smoke {marker}"
    result = await client.send(to=email_to, subject=subject,
                               body=("This is the ProcureAI live smoke test. If you can read this, "
                                     f"the deployment can reach real inboxes. Marker: {marker}\n"))
    if not result.ok:
        return Check("email", "FAIL", f"SMTP rejected the message: {result.error or 'unknown error'}")
    verified, note = await asyncio.to_thread(_imap_verify, subject, timeout_s=min(timeout_s, 90))
    return Check("email", "PASS", f"delivered to {email_to}; {note}",
                 {"provider_message_id": result.provider_message_id, "receipt_verified": verified})


# ------------------------------------------------------------------ whatsapp
async def check_whatsapp(whatsapp_to: str) -> Check:
    provider = (settings.whatsapp_provider or "fake").lower()
    if _is_fake(provider):
        strict = _strict_fail("whatsapp", "WHATSAPP_PROVIDER is a fake")
        return strict or Check("whatsapp", "SKIP", "WHATSAPP_PROVIDER=fake (set twilio or cloud_api)")
    recipient = whatsapp_to or os.environ.get("LIVE_SMOKE_WHATSAPP_TO", "")
    if not recipient:
        return Check("whatsapp", "SKIP" if not settings.strict_providers else "FAIL",
                     "no recipient — pass --whatsapp-to or set LIVE_SMOKE_WHATSAPP_TO to your own WhatsApp number")

    from app.adapters.registry import build

    try:
        client = build("whatsapp")
    except Exception as exc:  # noqa: BLE001
        return Check("whatsapp", "FAIL", f"adapter refused to build: {exc}")

    template = os.environ.get("LIVE_SMOKE_WHATSAPP_TEMPLATE", "")
    variables = [v for v in os.environ.get("LIVE_SMOKE_WHATSAPP_TEMPLATE_VARS", "").split("|") if v]
    if template:
        result = await client.send_template(to=recipient, template=template, variables=variables)
        what = f"template {template!r}"
    else:
        result = await client.send_text(to=recipient, body=f"ProcureAI live smoke {uuid.uuid4().hex[:8]} — ignore.")
        what = "session text message"
    if not result.ok:
        return Check("whatsapp", "FAIL", f"vendor rejected the {what}: {result.error or 'unknown error'}")
    return Check("whatsapp", "PASS", f"vendor accepted a {what} to {recipient}",
                 {"provider_message_id": result.provider_message_id, "kind": what})


# ------------------------------------------------------------------ telephony
async def check_telephony(call_to: str, *, timeout_s: int) -> Check:
    if not settings.voice_ai_enabled:
        return Check("telephony", "SKIP",
                     "VOICE_AI_ENABLED=false — the AI-voice path is deliberately off; "
                     "set it to true to place a live call")
    provider = (settings.telephony_provider or "fake").lower()
    if _is_fake(provider):
        strict = _strict_fail("telephony", "TELEPHONY_PROVIDER is a fake")
        return strict or Check("telephony", "SKIP", "TELEPHONY_PROVIDER=fake (set twilio)")
    recipient = call_to or os.environ.get("LIVE_SMOKE_CALL_TO", "")
    if not recipient:
        return Check("telephony", "SKIP" if not settings.strict_providers else "FAIL",
                     "no recipient — pass --call-to or set LIVE_SMOKE_CALL_TO to your own number")

    from app.adapters.registry import build

    try:
        client = build("telephony")
    except Exception as exc:  # noqa: BLE001
        return Check("telephony", "FAIL", f"adapter refused to build: {exc}")

    placed = await client.place_call(
        to=recipient,
        script="This is the ProcureAI smoke test. No quote is being requested. Goodbye.",
        disclosure="This call is placed by an AI assistant on behalf of Mantiq Technologies.",
        country="EG", language="ar", consent_required=True)
    if not placed.provider_call_id:
        return Check("telephony", "FAIL", f"vendor did not place the call: {placed.summary or placed.status}")

    # Poll the call resource until it settles — placing is not the same as proving the wire works.
    deadline = time.time() + timeout_s
    status = placed.status
    while time.time() < deadline and status in {"queued", "ringing", "initiated", "in-progress"}:
        await asyncio.sleep(2)
        status = (await client.fetch_call(placed.provider_call_id)).status
    terminal = {"completed", "busy", "no-answer", "failed", "canceled", "completed-with-errors"}
    settled = status in terminal or time.time() >= deadline
    if status == "failed":
        return Check("telephony", "FAIL", f"call {placed.provider_call_id} failed: {placed.summary}")
    return Check("telephony", "PASS",
                 f"call placed ({placed.provider_call_id}); status={status}"
                 + ("" if settled else " (still in flight at timeout)"),
                 {"provider_call_id": placed.provider_call_id, "status": status})


# ------------------------------------------------------------------ storage
async def check_storage() -> Check:
    bucket = (settings.aws_storage_bucket or "").strip()
    if not bucket:
        strict = _strict_fail("storage", "AWS_STORAGE_BUCKET is not set")
        return strict or Check("storage", "SKIP", "AWS_STORAGE_BUCKET unset — local disk is the dev default")

    from app.adapters.erp_fx_payment import S3Storage

    client = S3Storage(bucket, settings.aws_endpoint, settings.aws_access_key, settings.aws_secret_key)
    key = f"procureai-live-smoke/{uuid.uuid4().hex}.bin"
    blob = os.urandom(256)

    def _roundtrip() -> None:
        client.put(key, blob)
        try:
            assert client.get(key) == blob, "read-back mismatch"
            assert client.exists(key), "HEAD after PUT failed"
        finally:
            client.delete(key)

    try:
        await asyncio.to_thread(_roundtrip)
    except AssertionError as exc:
        return Check("storage", "FAIL", str(exc))
    except Exception as exc:  # noqa: BLE001
        return Check("storage", "FAIL", f"S3 round-trip failed: {type(exc).__name__}: {exc}"[:300])

    # A presigned URL is the only artifact a supplier's browser ever sees: prove it answers.
    try:
        await asyncio.to_thread(client.put, key + ".presign", blob)
        url = client.signed_url(key + ".presign", expires_in=120)
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(url)
        body = resp.content
        ok = resp.status_code == 200 and body == blob
        detail = f"signed URL answered HTTP {resp.status_code}"
        await asyncio.to_thread(client.delete, key + ".presign")
        if not ok:
            return Check("storage", "FAIL", detail + " (expected 200 with identical bytes)")
    except Exception as exc:  # noqa: BLE001
        return Check("storage", "FAIL", f"presigned GET failed: {type(exc).__name__}: {exc}"[:300])

    return Check("storage", "PASS", f"PUT/GET/HEAD/presigned-GET/DELETE round-trip on s3://{bucket}",
                 {"bucket": bucket, "endpoint": settings.aws_endpoint or "aws-default"})


# ------------------------------------------------------------------ search
async def check_search() -> Check:
    provider = (settings.search_provider or "fake").lower()
    if _is_fake(provider):
        # Discovery is an opt-in capability: a fake is a warning in production, not a failure.
        return Check("search", "SKIP", "SEARCH_PROVIDER=fake (set serpapi or brave for live discovery)")
    if not settings.search_api_key:
        return Check("search", "FAIL", f"SEARCH_PROVIDER={provider} but SEARCH_API_KEY is unset")

    from app.adapters.registry import build

    query = os.environ.get("LIVE_SMOKE_QUERY", "steel suppliers Egypt")
    try:
        client = build("search")
        hits = await client.search(query, limit=5, country="EG")
    except Exception as exc:  # noqa: BLE001
        return Check("search", "FAIL", f"live query failed: {type(exc).__name__}: {exc}"[:300])
    if not hits:
        return Check("search", "FAIL", f"the vendor answered but returned 0 results for {query!r}")
    return Check("search", "PASS", f"{provider} returned {len(hits)} results for {query!r}",
                 {"hits": len(hits), "first_url": hits[0].url})


# ------------------------------------------------------------------ runner
async def run_checks(*, only: list[str] | None = None, email_to: str = "",
                     whatsapp_to: str = "", call_to: str = "", timeout_s: int = 60) -> list[Check]:
    selected = [name for name in (only or ALL_CHECKS) if name in ALL_CHECKS]
    results: list[Check] = []
    for name in selected:
        if name == "llm":
            results.append(await check_llm())
        elif name == "email":
            results.append(await check_email(email_to, timeout_s=timeout_s))
        elif name == "whatsapp":
            results.append(await check_whatsapp(whatsapp_to))
        elif name == "telephony":
            results.append(await check_telephony(call_to, timeout_s=timeout_s))
        elif name == "storage":
            results.append(await check_storage())
        elif name == "search":
            results.append(await check_search())
    return results
