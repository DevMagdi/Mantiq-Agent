"""Adapter contracts. Every external interface ships: interface + real impl + Fake impl."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ------------------------------------------------------------------ results
@dataclass
class SendResult:
    ok: bool
    provider_message_id: str = ""
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class InboundMessage:
    channel: str
    external_id: str
    sender: str
    subject: str
    body: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    in_reply_to: str = ""
    received_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    source: str = "web"


@dataclass
class SupplierCandidateData:
    name: str
    name_ar: str = ""
    website: str = ""
    domain: str = ""
    country: str = "EG"
    city: str = ""
    email: str = ""
    phone: str = ""
    source_url: str = ""
    confidence: float = 0.5
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallResult:
    provider_call_id: str
    status: str
    disclosure_played: bool = False
    consent_given: bool = False
    transcript: str = ""
    duration_seconds: int = 0
    recording_uri: str = ""
    summary: str = ""


@dataclass
class ExtractionPayload:
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)
    pages: int = 1
    source: str = "text"


@dataclass
class OcrResult:
    text: str
    confidence: float = 0.9
    blocks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TranscriptResult:
    text: str
    language: str = "ar-EG"
    confidence: float = 0.9


@dataclass
class FxQuote:
    base: str
    quote: str
    rate: str  # decimal as string to keep exactness
    as_of: str
    source: str = "manual"


@dataclass
class PaymentResult:
    ok: bool
    reference: str = ""
    url: str = ""
    status: str = "pending"
    error: str = ""


@dataclass
class ErpResult:
    ok: bool
    reference: str = ""
    error: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ protocols
@runtime_checkable
class EmailProvider(Protocol):
    name: str

    async def send(self, *, to: str, subject: str, body: str, html: str = "",
                   attachments: list[dict[str, Any]] | None = None,
                   reply_to: str = "") -> SendResult: ...

    async def poll_inbox(self, limit: int = 25) -> list[InboundMessage]: ...


@runtime_checkable
class WhatsAppProvider(Protocol):
    """WhatsApp is a first-class channel: text, templates, documents, interactive quotes and receipts.

    `supports_templates` / `supports_documents` / `supports_interactive` let the service degrade to
    plain text when a provider (or a tenant's approved template set) cannot do the richer form,
    instead of failing the send.
    """

    name: str

    async def send_text(self, *, to: str, body: str, template: str = "") -> SendResult: ...

    async def send_template(self, *, to: str, template: str, variables: list[str]) -> SendResult: ...

    async def send_document(self, *, to: str, filename: str, data: bytes, caption: str = "",
                            mime: str = "") -> SendResult: ...

    async def send_interactive(self, *, to: str, body: str, options: list[str],
                               flow: str = "") -> SendResult: ...

    async def download_media(self, media_id: str) -> tuple[bytes, str]: ...

    def verify_webhook(self, body: bytes, signature: str) -> bool: ...

    def parse_webhook(self, payload: dict[str, Any]) -> list[InboundMessage]: ...

    def parse_statuses(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...


@runtime_checkable
class TelephonyProvider(Protocol):
    name: str

    async def place_call(self, *, to: str, script: str, disclosure: str, country: str,
                         language: str, consent_required: bool) -> CallResult: ...

    async def fetch_call(self, provider_call_id: str) -> CallResult: ...


@runtime_checkable
class OcrProvider(Protocol):
    name: str

    async def extract(self, *, data: bytes, filename: str, languages: str = "ara+eng") -> OcrResult: ...


@runtime_checkable
class SttProvider(Protocol):
    name: str

    async def transcribe(self, *, data: bytes, filename: str, language: str = "ar") -> TranscriptResult: ...


@runtime_checkable
class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, limit: int = 10, country: str = "EG") -> list[SearchHit]: ...

    async def discover_suppliers(self, query: str, *, country: str = "EG",
                                 limit: int = 24) -> list[SupplierCandidateData]: ...


@runtime_checkable
class ErpConnector(Protocol):
    name: str

    async def test_connection(self) -> ErpResult: ...

    async def import_requisitions(self, limit: int = 20) -> list[dict[str, Any]]: ...

    async def export_purchase_order(self, po: dict[str, Any]) -> ErpResult: ...


@runtime_checkable
class FxProvider(Protocol):
    name: str

    async def rate(self, base: str, quote: str) -> FxQuote | None: ...


@runtime_checkable
class PaymentGateway(Protocol):
    name: str

    async def create_invoice(self, *, amount: str, currency: str, reference: str,
                             description: str) -> PaymentResult: ...

    async def verify_webhook(self, body: bytes, signature: str) -> bool: ...


# ------------------------------------------------------------------ helpers
def fake_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return f"{prefix}_{digest}"


class AdapterRegistry:
    """Lazily resolved singletons so tests can swap any adapter."""

    def __init__(self) -> None:
        self._overrides: dict[str, Any] = {}
        self._cache: dict[str, Any] = {}

    def override(self, name: str, impl: Any) -> None:
        self._overrides[name] = impl
        self._cache.pop(name, None)

    def clear_overrides(self) -> None:
        self._overrides.clear()
        self._cache.clear()

    def resolve(self, name: str) -> Any:
        if name in self._overrides:
            return self._overrides[name]
        if name in self._cache:
            return self._cache[name]
        from app.adapters import registry as factory

        impl = factory.build(name)
        self._cache[name] = impl
        return impl


registry = AdapterRegistry()
