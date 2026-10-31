"""Tenant-managed integrations: one catalogue, one store, one honest status per provider.

Before this module the Integrations screen was decorative: it wrote an `integration_config` row that
nothing ever read, so a tenant could "configure" SMTP and the platform kept sending with the fake
adapter. The rule implemented here is the opposite:

* **the catalogue is the contract** — every provider lists the fields it needs, which of them are
  secrets, and which are required; the UI renders those fields instead of an empty JSON box;
* **the store is the truth** — `adapter_for()` returns the tenant's adapter whenever a complete,
  enabled configuration exists, and only then falls back to the platform adapter;
* **status is computed, never stored as a wish** — `live` requires a complete configuration *and*
  the last probe to have succeeded; an incomplete configuration is `error`, a disabled one is
  `disabled`, and a platform fake is `sandbox`.

Secrets are envelope-encrypted with the tenant's key (`app.core.crypto`), never returned in full and
never written to the audit log — the log records *which* keys changed, never their values.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import registry
from app.adapters.email import SmtpEmailProvider
from app.adapters.search import build_search_provider
from app.adapters.telephony_ocr_stt import TwilioTelephonyProvider
from app.adapters.twilio import TwilioSmsProvider, TwilioWhatsAppProvider, fetch_account
from app.adapters.whatsapp import CloudWhatsAppProvider
from app.core.config import settings
from app.core.crypto import decrypt_text, encrypt_text
from app.core.providers import FAKE_PROVIDERS, ProviderNotConfigured
from app.db.base import utcnow
from app.db.models.entities import IntegrationConfig, Tenant

log = logging.getLogger("procureai.integrations")

# ------------------------------------------------------------------ catalogue
@dataclass(frozen=True)
class Field:
    """One input on the screen, and the validation rule that goes with it."""

    key: str
    label_en: str
    label_ar: str
    kind: str = "text"          # text | password | number | url | email | tel | select
    required: bool = True
    secret: bool = False
    placeholder: str = ""
    help_en: str = ""
    help_ar: str = ""
    default: str = ""
    options: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label_en": self.label_en, "label_ar": self.label_ar,
            "kind": self.kind, "required": self.required, "secret": self.secret,
            "placeholder": self.placeholder, "help_en": self.help_en, "help_ar": self.help_ar,
            "default": self.default,
            "options": [{"value": value, "label": label} for value, label in self.options],
        }


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class Kind:
    key: str
    label_en: str
    label_ar: str
    description_en: str
    description_ar: str
    providers: tuple[Provider, ...] = ()
    tenant_managed: bool = True

    @property
    def secret_keys(self) -> set[str]:
        return {f.key for provider in self.providers for f in provider.fields if f.secret}

    def provider(self, key: str) -> Provider | None:
        for provider in self.providers:
            if provider.key == key:
                return provider
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label_en": self.label_en, "label_ar": self.label_ar,
            "description_en": self.description_en, "description_ar": self.description_ar,
            "tenant_managed": self.tenant_managed,
            "providers": [{"key": p.key, "label": p.label,
                           "fields": [f.as_dict() for f in p.fields]} for p in self.providers],
        }


_BASE_URL_FIELD = Field(
    "base_url", "Base URL", "عنوان الخدمة", kind="url", required=False,
    placeholder="https://api.example.com",
    help_en="Only for a gateway or a self-hosted endpoint.",
    help_ar="للبوابة أو خادم مستضاف ذاتيًا فقط.",
)

LLM = Kind(
    "llm", "Language model", "نموذج اللغة",
    "Drafting, offer extraction, negotiation text and the assistant.",
    "الصياغة واستخراج العروض ونصوص التفاوض والمساعد.",
    providers=(
        Provider("anthropic", "Anthropic Claude", (
            Field("api_key", "API key", "مفتاح الـ API", kind="password", secret=True,
                  placeholder="sk-ant-…",
                  help_en="Console → API keys. Stored encrypted, never shown again.",
                  help_ar="من لوحة Anthropic ← مفاتيح API. يُخزَّن مشفَّرًا ولا يُعرض مرة أخرى."),
            Field("model", "Model", "النموذج", placeholder="claude-sonnet-4-5-20250929",
                  help_en="Exact model id.", help_ar="معرّف النموذج الكامل."),
            _BASE_URL_FIELD,
        )),
        Provider("openai_compatible", "OpenAI / compatible", (
            Field("api_key", "API key", "مفتاح الـ API", kind="password", secret=True,
                  placeholder="sk-…",
                  help_en="Leave empty only for a self-hosted endpoint without auth.",
                  help_ar="اتركه فارغًا فقط لخادم ذاتي بدون مصادقة."),
            Field("model", "Model", "النموذج", placeholder="gpt-4.1"),
            Field("base_url", "Base URL", "عنوان الخدمة", kind="url", required=True,
                  placeholder="https://api.openai.com/v1",
                  help_en="Include the version segment (/v1).", help_ar="أدرِج مقطع الإصدار (/v1)."),
        )),
    ),
)

WHATSAPP = Kind(
    "whatsapp", "WhatsApp", "واتساب",
    "Supplier conversations: invitations, quote requests and inbound replies.",
    "محادثات الموردين: الدعوات وطلبات الأسعار والردود الواردة.",
    providers=(
        Provider("cloud_api", "Meta WhatsApp Cloud API", (
            Field("token", "Access token", "رمز الوصول", kind="password", secret=True,
                  help_en="Permanent system-user token.", help_ar="رمز مستخدم نظام دائم."),
            Field("phone_number_id", "Phone number ID", "معرّف رقم الهاتف",
                  placeholder="123456789012345",
                  help_en="Meta → WhatsApp → API setup.", help_ar="من Meta ← واتساب ← إعداد API."),
            Field("verify_token", "Webhook verify token", "رمز تحقّق الـ webhook", required=False,
                  help_en="Echoed back by Meta during the verification handshake.",
                  help_ar="تعيده Meta أثناء مصافحة التحقّق."),
            Field("app_secret", "App secret", "سرّ التطبيق", kind="password", secret=True,
                  required=False,
                  help_en="Verifies inbound webhook signatures.", help_ar="للتحقّق من توقيع الـ webhook."),
            Field("base_url", "API base URL", "عنوان الـ API", kind="url", required=False,
                  placeholder="https://graph.facebook.com/v20.0",
                  help_en="Only for a regional endpoint or a proxy — include the version segment.",
                  help_ar="لنقطة إقليمية أو بوابة فقط — أدرِج مقطع الإصدار."),
        )),
        Provider("twilio", "Twilio WhatsApp", (
            Field("account_sid", "Account SID", "Account SID", placeholder="AC…"),
            Field("auth_token", "Auth token", "Auth token", kind="password", secret=True),
            Field("from_number", "From number", "رقم الإرسال", kind="tel",
                  placeholder="whatsapp:+14155238886",
                  help_en="Must be the WhatsApp-enabled sender.", help_ar="يجب أن يكون المرسل المفعّل لواتساب."),
            _BASE_URL_FIELD,
        )),
    ),
)

SMS = Kind(
    "sms", "SMS", "الرسائل النصية",
    "Fallback channel for suppliers who have a phone but no WhatsApp.",
    "قناة بديلة للموردين الذين لديهم هاتف بدون واتساب.",
    providers=(
        Provider("twilio", "Twilio SMS", (
            Field("account_sid", "Account SID", "Account SID", placeholder="AC…"),
            Field("auth_token", "Auth token", "Auth token", kind="password", secret=True),
            Field("from_number", "From number", "رقم الإرسال", kind="tel", placeholder="+12025550123"),
            _BASE_URL_FIELD,
        )),
    ),
)

TELEPHONY = Kind(
    "telephony", "Voice calls", "المكالمات الصوتية",
    "Outbound AI calls with disclosure and consent, and the human script.",
    "مكالمات آلية مع إفصاح وموافقة، ونص للمكالمة البشرية.",
    providers=(
        Provider("twilio", "Twilio Voice", (
            Field("account_sid", "Account SID", "Account SID", placeholder="AC…"),
            Field("auth_token", "Auth token", "Auth token", kind="password", secret=True),
            Field("from_number", "From number", "رقم الإرسال", kind="tel", placeholder="+12025550123"),
            _BASE_URL_FIELD,
        )),
    ),
)

EMAIL = Kind(
    "email", "Email", "البريد الإلكتروني",
    "RFQ dispatch, reminders and supplier replies.",
    "إرسال طلبات الأسعار والتذكيرات وردود الموردين.",
    providers=(
        Provider("smtp", "SMTP", (
            Field("host", "SMTP host", "خادم SMTP", placeholder="smtp.office365.com"),
            Field("port", "Port", "المنفذ", kind="number", default="587", placeholder="587"),
            Field("user", "Username", "اسم المستخدم", required=False,
                  placeholder="rfq@yourcompany.com"),
            Field("password", "Password", "كلمة المرور", kind="password", secret=True,
                  required=False),
            Field("from_address", "From address", "عنوان المرسل", kind="email",
                  placeholder="rfq@yourcompany.com"),
            Field("imap_host", "IMAP host", "خادم IMAP", required=False,
                  help_en="For polling supplier replies.", help_ar="لجلب ردود الموردين."),
        )),
    ),
)

SEARCH = Kind(
    "search", "Supplier discovery", "استكشاف الموردين",
    "Finds candidate suppliers; every candidate still needs buyer review.",
    "يقترح موردين مرشحين؛ وكل مرشح يبقى بحاجة لمراجعة المشتري.",
    providers=(
        Provider("serpapi", "SerpAPI", (
            Field("api_key", "API key", "مفتاح الـ API", kind="password", secret=True,
                  placeholder="serpapi key"),
        )),
        Provider("brave", "Brave Search", (
            Field("api_key", "API key", "مفتاح الـ API", kind="password", secret=True,
                  placeholder="brave subscription token"),
            _BASE_URL_FIELD,
        )),
    ),
)

PLATFORM_ONLY = (
    Kind("ocr", "Document OCR", "قراءة المستندات",
         "Reads scanned quotations.", "قراءة عروض الأسعار الممسوحة.", tenant_managed=False),
    Kind("payment", "Payments", "المدفوعات",
         "Records a payment intent only — execution stays outside the platform.",
         "تسجيل نيّة الدفع فقط — التنفيذ خارج المنصة.", tenant_managed=False),
    Kind("erp", "ERP", "نظام ERP",
         "Purchase-order export to accounting.", "تصدير أوامر الشراء إلى المحاسبة.",
         tenant_managed=False),
    Kind("stt", "Speech to text", "تحويل الكلام إلى نص",
         "Transcribes supplier calls and spoken questions.",
         "تحويل المكالمات والأسئلة الصوتية إلى نص.", tenant_managed=False),
)

CATALOGUE: dict[str, Kind] = {kind.key: kind for kind in
                              (LLM, WHATSAPP, SMS, TELEPHONY, EMAIL, SEARCH, *PLATFORM_ONLY)}

#: Which platform setting backs each kind (for the "platform default" column of the screen).
PLATFORM_SETTING = {
    "llm": "llm_provider", "whatsapp": "whatsapp_provider", "sms": "sms_provider",
    "telephony": "telephony_provider", "email": "email_provider", "search": "search_provider",
    "ocr": "ocr_provider", "payment": "payment_provider", "erp": "erp_provider",
    "stt": "stt_provider",
}

STATUSES = ("live", "sandbox", "not_configured", "error", "disabled")


def platform_provider(kind: str) -> str:
    return str(getattr(settings, PLATFORM_SETTING.get(kind, "email_provider"), "") or "fake")


def platform_is_live(kind: str) -> bool:
    return (platform_provider(kind) or "").lower() not in FAKE_PROVIDERS


# ------------------------------------------------------------------ storage helpers
async def _row(session: AsyncSession, tenant_id: str, kind: str) -> IntegrationConfig | None:
    return (await session.execute(
        select(IntegrationConfig)
        .where(IntegrationConfig.tenant_id == tenant_id, IntegrationConfig.kind == kind)
        .order_by(IntegrationConfig.created_at.asc())
    )).scalars().first()


async def _secrets(session: AsyncSession, tenant: Tenant,
                   row: IntegrationConfig | None) -> dict[str, str]:
    if not row or not row.secrets_enc:
        return {}
    try:
        import json

        return json.loads(await decrypt_text(session, tenant, row.secrets_enc, row.key_id) or "{}")
    except Exception as exc:  # noqa: BLE001 - a wrong key must not break the whole screen
        log.warning("integration %s: secrets could not be decrypted (%s)", row.kind, type(exc).__name__)
        return {}


def _provider_of(row: IntegrationConfig | None, kind: Kind) -> str:
    if row and row.config and row.config.get("provider"):
        return str(row.config["provider"])
    if kind.providers:
        return kind.providers[0].key
    return platform_provider(kind.key)


def missing_fields(kind: Kind, provider_key: str, config: dict, secrets: dict) -> list[str]:
    """Required inputs that are still empty — the reason a save cannot be `live` yet."""
    provider = kind.provider(provider_key)
    if not provider:
        return ["provider"]
    merged = {**{f.key: f.default for f in provider.fields}, **config, **secrets}
    return [f.key for f in provider.fields if f.required and not str(merged.get(f.key) or "").strip()]


def mask(value: str) -> str:
    """Show the last four characters only — enough to recognise, not enough to use."""
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return f"{'•' * min(12, len(value) - 4)}{value[-4:]}"


def status_for(kind: Kind, row: IntegrationConfig | None, missing: list[str]) -> tuple[str, str]:
    """(status, provider source). `live` is never claimed on hope."""
    if not kind.tenant_managed:
        return ("live" if platform_is_live(kind.key) else "sandbox", "platform")
    if row is None:
        return ("live" if platform_is_live(kind.key) else "sandbox", "platform")
    if not row.enabled:
        return ("disabled", "tenant")
    if missing:
        return ("error", "tenant")
    if row.status == "error":
        return ("error", "tenant")
    return ("live", "tenant")


# ------------------------------------------------------------------ public snapshot
async def snapshot(session: AsyncSession, tenant: Tenant) -> dict[str, Any]:
    """Everything the screen needs, with no secret in it."""
    rows = {row.kind: row for row in (await session.execute(
        select(IntegrationConfig).where(IntegrationConfig.tenant_id == tenant.id)
    )).scalars().all()}

    items: list[dict[str, Any]] = []
    for key, kind in CATALOGUE.items():
        row = rows.get(key)
        secrets = await _secrets(session, tenant, row) if row else {}
        provider_key = _provider_of(row, kind)
        config = dict(row.config or {}) if row else {}
        missing = missing_fields(kind, provider_key, config, secrets) if kind.tenant_managed else []
        status, source = status_for(kind, row, missing)
        items.append({
            "kind": key,
            "provider": provider_key,
            "provider_source": source,
            "platform_provider": platform_provider(key),
            "status": status,
            "enabled": bool(row.enabled) if row else True,
            "tenant_managed": kind.tenant_managed,
            "config": {k: v for k, v in config.items() if k != "provider"},
            "secrets_saved": sorted(secrets.keys()),
            "masked": {k: mask(v) for k, v in secrets.items() if v},
            "missing": missing,
            "last_tested_at": row.last_tested_at.isoformat() if row and row.last_tested_at else None,
            "last_error": row.last_error if row else "",
            "last_test_ok": bool(row and row.status == "live") if row else None,
        })
    return {
        "items": items,
        "catalogue": [kind.as_dict() for kind in CATALOGUE.values()],
        "platform_adapters": {key: platform_provider(key) for key in CATALOGUE},
        "statuses": list(STATUSES),
    }


# ------------------------------------------------------------------ adapter construction
def build_from(kind: str, provider_key: str, config: dict, secrets: dict) -> Any:
    """Build a real adapter from a stored tenant configuration.

    Raises :class:`ProviderNotConfigured` when a required value is missing, so a half-filled form can
    never silently degrade into the fake adapter.
    """
    kind_spec = CATALOGUE.get(kind)
    if not kind_spec or not kind_spec.tenant_managed:
        raise ProviderNotConfigured(f"{kind}: not tenant-configurable", problems=[f"{kind}: read-only"])
    merged = {**config, **secrets}
    missing = missing_fields(kind_spec, provider_key, config, secrets)
    if missing:
        raise ProviderNotConfigured(
            f"{kind}: {provider_key} is missing {', '.join(missing)}",
            problems=[f"{kind}: missing {', '.join(missing)}"],
        )

    def value(key: str, default: str = "") -> str:
        return str(merged.get(key) or default).strip()

    if kind == "llm":
        from app.agents.llm.providers import AnthropicProvider, OpenAICompatibleProvider

        if provider_key == "anthropic":
            return AnthropicProvider(value("api_key"), value("model"), value("base_url") or
                                     "https://api.anthropic.com")
        return OpenAICompatibleProvider(value("api_key"), value("model"), value("base_url"))
    if kind == "email":
        return SmtpEmailProvider(value("host"), int(value("port", "587") or 587), value("user"),
                                 value("password"), value("from_address"),
                                 imap_host=value("imap_host") or None)
    if kind == "whatsapp":
        if provider_key == "cloud_api":
            return CloudWhatsAppProvider(value("token"), value("phone_number_id"),
                                         value("app_secret"), value("quote_flow_id"),
                                         base_url=value("base_url"))
        return TwilioWhatsAppProvider(value("account_sid"), value("auth_token"),
                                      value("from_number"), base_url=value("base_url"))
    if kind == "sms":
        return TwilioSmsProvider(value("account_sid"), value("auth_token"), value("from_number"),
                                 base_url=value("base_url"))
    if kind == "telephony":
        return TwilioTelephonyProvider(value("account_sid"), value("auth_token"),
                                       value("from_number"), base_url=value("base_url"))
    if kind == "search":
        if provider_key == "brave":
            from app.adapters.search import BraveSearchProvider

            return BraveSearchProvider(value("api_key"), base_url=value("base_url") or
                                       "https://api.search.brave.com")
        return build_search_provider("serpapi", value("api_key"), value("base_url"))
    raise ProviderNotConfigured(f"{kind}: unsupported provider {provider_key}",
                                problems=[f"{kind}: {provider_key}"])


async def configured(session: AsyncSession, tenant: Tenant,
                     kind: str) -> tuple[str, dict, dict] | None:
    """(provider, config, secrets) for an enabled, complete tenant integration, else None."""
    kind_spec = CATALOGUE.get(kind)
    if not kind_spec or not kind_spec.tenant_managed:
        return None
    row = await _row(session, tenant.id, kind)
    if not row or not row.enabled:
        return None
    secrets = await _secrets(session, tenant, row)
    provider_key = _provider_of(row, kind_spec)
    config = dict(row.config or {})
    if missing_fields(kind_spec, provider_key, config, secrets):
        return None
    return provider_key, config, secrets


async def adapter_for(session: AsyncSession, tenant: Tenant, kind: str) -> Any:
    """The adapter the running system must use: the tenant's, or the platform's.

    A tenant configuration wins only when it is enabled and complete. Disabling an integration is
    therefore a safe, reversible action: traffic returns to the platform adapter immediately.
    """
    kind_spec = CATALOGUE.get(kind)
    if not kind_spec or not kind_spec.tenant_managed:
        return registry.resolve(kind)
    ready = await configured(session, tenant, kind)
    if ready is None:
        return registry.resolve(kind)
    provider_key, config, secrets = ready
    return build_from(kind, provider_key, config, secrets)


# ------------------------------------------------------------------ save / enable / probe
async def save(session: AsyncSession, tenant: Tenant, kind_key: str, *, provider: str,
               config: dict[str, Any], secrets: dict[str, str], enabled: bool) -> dict[str, Any]:
    """Validate, encrypt and store one integration; returns the new snapshot item."""
    kind = CATALOGUE.get(kind_key)
    if not kind:
        raise ValueError(f"unknown integration kind: {kind_key}")
    if not kind.tenant_managed:
        raise ValueError(f"{kind_key} is configured on the platform, not per tenant")
    provider_key = provider or (kind.providers[0].key if kind.providers else "")
    if not kind.provider(provider_key):
        raise ValueError(f"{kind_key}: unknown provider {provider_key}")

    row = await _row(session, tenant.id, kind_key)
    existing = await _secrets(session, tenant, row) if row else {}

    # An empty secret field means "keep what is stored" — the screen only ever shows a mask.
    merged_secrets = dict(existing)
    changed: list[str] = []
    for key in kind.secret_keys:
        incoming = str(secrets.get(key) or "").strip()
        if incoming:
            if incoming != existing.get(key):
                changed.append(key)
            merged_secrets[key] = incoming
    plain = {k: v for k, v in (config or {}).items() if k not in kind.secret_keys and k != "provider"}
    plain["provider"] = provider_key

    encrypted, key_id = ("", "")
    if merged_secrets:
        import json

        encrypted, key_id = await encrypt_text(session, tenant, json.dumps(merged_secrets))

    if row is None:
        row = IntegrationConfig(tenant_id=tenant.id, kind=kind_key, name=kind.label_en)
        session.add(row)
    row.config = plain
    row.secrets_enc = encrypted
    row.key_id = key_id
    row.enabled = bool(enabled)
    row.status = "error" if missing_fields(kind, provider_key, plain, merged_secrets) else "configured"
    row.last_error = ""
    await session.flush()

    # A changed credential invalidates any cached LLM client.
    if kind_key == "llm":
        from app.agents.llm import clear_cache

        clear_cache()
    log.info("integration saved: tenant=%s kind=%s provider=%s secrets_changed=%s",
             tenant.id, kind_key, provider_key, changed)
    return {"id": row.id, "kind": kind_key, "provider": provider_key,
            "secrets_changed": changed, "enabled": row.enabled,
            "missing": missing_fields(kind, provider_key, plain, merged_secrets)}


async def set_enabled(session: AsyncSession, tenant: Tenant, kind_key: str, enabled: bool) -> dict:
    row = await _row(session, tenant.id, kind_key)
    if not row:
        raise ValueError(f"{kind_key} is not configured")
    row.enabled = bool(enabled)
    if not enabled:
        row.status = "disabled"
    else:
        kind = CATALOGUE[kind_key]
        secrets = await _secrets(session, tenant, row)
        provider_key = _provider_of(row, kind)
        row.status = ("error" if missing_fields(kind, provider_key, dict(row.config or {}), secrets)
                      else "configured")
    await session.flush()
    if kind_key == "llm":
        from app.agents.llm import clear_cache

        clear_cache()
    return {"kind": kind_key, "enabled": row.enabled, "status": row.status}


async def probe(session: AsyncSession, tenant: Tenant, kind_key: str) -> dict[str, Any]:
    """Make one real call to the provider and record what happened.

    Never returns `ok: true` because "the adapter resolved": a fake provider is reported as
    `sandbox`, an unreachable one as `error` with the vendor's own message.
    """
    kind = CATALOGUE.get(kind_key)
    if not kind:
        raise ValueError(f"unknown integration kind: {kind_key}")
    row = await _row(session, tenant.id, kind_key)
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    if not kind.tenant_managed:
        ok = platform_is_live(kind_key)
        detail = (f"platform provider: {platform_provider(kind_key)}" if ok else
                  "no real provider on the platform; this integration is not configured")
        return {"ok": ok, "status": "live" if ok else "sandbox", "detail": detail,
                "latency_ms": elapsed(), "probe": "platform"}

    secrets = await _secrets(session, tenant, row) if row else {}
    provider_key = _provider_of(row, kind)
    config = dict(row.config or {}) if row else {}
    missing = missing_fields(kind, provider_key, config, secrets)
    if row is None or missing:
        return {"ok": False, "status": "not_configured" if row is None else "error",
                "detail": ("nothing saved yet" if row is None else
                           f"missing: {', '.join(missing)}"),
                "latency_ms": elapsed(), "probe": "config"}

    ok, detail, status = await _run_probe(kind_key, provider_key, config, secrets)

    row.last_tested_at = utcnow()
    row.last_error = "" if ok else detail
    row.status = status
    await session.flush()
    return {"ok": ok, "status": status, "detail": detail, "latency_ms": elapsed(),
            "probe": provider_key}


async def _run_probe(kind_key: str, provider_key: str, config: dict,
                     secrets: dict) -> tuple[bool, str, str]:
    """(ok, human detail, status). One vendor call each; no simulation."""
    merged = {**config, **secrets}

    def value(key: str, default: str = "") -> str:
        return str(merged.get(key) or default).strip()

    try:
        if kind_key == "llm":
            adapter = build_from(kind_key, provider_key, config, secrets)
            from app.agents.llm.base import LLMMessage, LLMRequest

            response = await adapter.complete(LLMRequest(
                task="summary", messages=[LLMMessage("user", "Reply with the word pong.")],
                context={"facts": {"ping": "pong"}}, max_tokens=16,
            ))
            reply = (response.text or "").strip()
            if not reply and not response.data:
                return False, "the model returned an empty answer", "error"
            return True, (f"{adapter.name}:{response.model} · {response.latency_ms} ms · "
                          f"reply “{reply[:40]}”"), "live"
        if kind_key == "email":
            adapter = build_from(kind_key, provider_key, config, secrets)
            ok, detail = await adapter.verify()
            return ok, detail, "live" if ok else "error"
        if kind_key == "whatsapp" and provider_key == "cloud_api":
            base = (value("base_url") or "https://graph.facebook.com/v20.0").rstrip("/")
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{base}/{value('phone_number_id')}",
                                        headers={"Authorization": f"Bearer {value('token')}"},
                                        params={"fields": "display_phone_number,verified_name"})
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code >= 400:
                error = (body.get("error") or {}).get("message", "") if isinstance(body, dict) else ""
                return False, f"Meta {resp.status_code}: {error or resp.text[:200]}", "error"
            name = body.get("verified_name", "") if isinstance(body, dict) else ""
            number = body.get("display_phone_number", "") if isinstance(body, dict) else ""
            return True, f"Meta Cloud API · {name or 'phone'} {number}".strip(), "live"
        if kind_key in {"whatsapp", "sms", "telephony"}:
            result = await fetch_account(value("account_sid"), value("auth_token"), value("base_url"))
            if not result.ok:
                return False, f"Twilio {result.status_code or 'unreachable'}: {result.message}"[:300], "error"
            friendly = result.body.get("friendly_name", "") if isinstance(result.body, dict) else ""
            return True, f"Twilio account {friendly or value('account_sid')[:8] + '…'} verified", "live"
        if kind_key == "search":
            adapter = build_from(kind_key, provider_key, config, secrets)
            hits = await adapter.search("steel supplier", limit=1)
            return True, f"{adapter.name}: {len(hits)} result(s) for a test query", "live"
    except ProviderNotConfigured as exc:
        return False, str(exc)[:300], "error"
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}: {exc}"[:300], "error"
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never takes the screen down
        return False, f"{type(exc).__name__}: {exc}"[:300], "error"
    return False, f"no probe for {kind_key}/{provider_key}", "error"
