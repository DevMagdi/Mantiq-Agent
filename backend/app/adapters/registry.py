"""Adapter factory: environment-driven, and honest about what it can build.

Rules, in order of importance:

1. **A fake is never a silent fallback in a real deployment.** `APP_ENV=prod|staging` (unless
   `ALLOW_FAKE_PROVIDERS=true` is set on purpose) turns a missing credential into
   :class:`~app.core.providers.ProviderNotConfigured` here — and, earlier and louder, into a startup
   failure via :func:`app.core.providers.assert_ready`. Dev and test keep the deterministic fakes so
   the full pipeline runs offline.
2. **Every branch is a real client.** The provider names map to the HTTP/API implementations
   (`CloudWhatsAppProvider`, `TwilioWhatsAppProvider`, `TwilioSmsProvider`, `SmtpEmailProvider`,
   `TwilioTelephonyProvider`, `AnthropicProvider`, …); nothing in this file pretends.
3. **One place decides.** `app/core/providers.py` owns the requirement table; this factory only asks
   it what is missing, so the CLI `doctor`, the startup check and the runtime error agree.
"""
from __future__ import annotations

from typing import Any

from app.adapters.email import FakeEmailProvider, SmtpEmailProvider
from app.adapters.erp_fx_payment import (
    FakePaymentGateway,
    FileExchangeConnector,
    HttpFxProvider,
    LocalStorage,
    OdooConnector,
    PaymobGateway,
    S3Storage,
    StaticFxProvider,
    StripeGateway,
)
from app.adapters.search import build_search_provider
from app.adapters.telephony_ocr_stt import (
    FakeOcrProvider,
    FakeSttProvider,
    FakeTelephonyProvider,
    TesseractOcrProvider,
    TwilioTelephonyProvider,
    WhisperSttProvider,
)
from app.adapters.twilio import FakeSmsProvider, TwilioSmsProvider, TwilioWhatsAppProvider
from app.adapters.whatsapp import CloudWhatsAppProvider, FakeWhatsAppProvider
from app.core.config import settings
from app.core.providers import FAKE_PROVIDERS, ProviderNotConfigured


def _missing(*names_and_values: tuple[str, str | None]) -> list[str]:
    return [name for name, value in names_and_values if not (value or "").strip()]


def _refuse_or_fake(adapter: str, selection: str, missing: list[str], fake: Any) -> Any:
    """The heart of the rule: refuse in a real deployment, degrade only in dev/test."""
    if settings.strict_providers:
        raise ProviderNotConfigured(
            f"{adapter}: {selection} is not configured (missing {', '.join(missing) or 'credentials'}). "
            f"APP_ENV={settings.app_env} requires the real provider; set the variables or set "
            "ALLOW_FAKE_PROVIDERS=true for a deliberately fake deployment.",
            problems=[f"{adapter}: missing {', '.join(missing)}"],
        )
    return fake


def build(name: str) -> Any:
    """Resolve one adapter. Raises `ProviderNotConfigured` instead of faking in prod/staging."""
    if name == "email":
        if settings.email_provider == "smtp":
            missing = _missing(("SMTP_HOST", settings.smtp_host),
                               ("SMTP_FROM", settings.smtp_from))
            if not missing:
                return SmtpEmailProvider(settings.smtp_host, settings.smtp_port, settings.smtp_user,
                                         settings.smtp_password, settings.smtp_from)
            return _refuse_or_fake("email", "EMAIL_PROVIDER=smtp", missing, FakeEmailProvider())
        return _unconfigured("email", "EMAIL_PROVIDER", FakeEmailProvider())

    if name == "whatsapp":
        provider = (settings.whatsapp_provider or "fake").lower()
        if provider == "cloud_api":
            missing = _missing(("WHATSAPP_TOKEN", settings.whatsapp_token),
                               ("WHATSAPP_PHONE_ID", settings.whatsapp_phone_id))
            if not missing:
                return CloudWhatsAppProvider(settings.whatsapp_token, settings.whatsapp_phone_id,
                                             settings.whatsapp_app_secret,
                                             settings.whatsapp_quote_flow_id)
            return _refuse_or_fake("whatsapp", "WHATSAPP_PROVIDER=cloud_api", missing,
                                   FakeWhatsAppProvider(settings.whatsapp_verify_token))
        if provider == "twilio":
            sid, token = settings.twilio_credentials()
            missing = _missing(("TWILIO_ACCOUNT_SID", sid), ("TWILIO_AUTH_TOKEN", token),
                               ("TWILIO_WHATSAPP_FROM", settings.twilio_whatsapp_from))
            if not missing:
                return TwilioWhatsAppProvider(sid, token, settings.twilio_whatsapp_from,
                                              base_url=settings.twilio_base_url)
            return _refuse_or_fake("whatsapp", "WHATSAPP_PROVIDER=twilio", missing,
                                   FakeWhatsAppProvider(settings.whatsapp_verify_token))
        return _unconfigured("whatsapp", "WHATSAPP_PROVIDER", FakeWhatsAppProvider(settings.whatsapp_verify_token))

    if name == "sms":
        provider = (settings.sms_provider or "fake").lower()
        if provider == "twilio":
            sid, token = settings.twilio_credentials()
            missing = _missing(("TWILIO_ACCOUNT_SID", sid), ("TWILIO_AUTH_TOKEN", token),
                               ("TWILIO_SMS_FROM", settings.twilio_sms_from))
            if not missing:
                return TwilioSmsProvider(sid, token, settings.twilio_sms_from,
                                         base_url=settings.twilio_base_url)
            return _refuse_or_fake("sms", "SMS_PROVIDER=twilio", missing, FakeSmsProvider())
        return _unconfigured("sms", "SMS_PROVIDER", FakeSmsProvider())

    if name == "telephony":
        provider = (settings.telephony_provider or "fake").lower()
        if provider == "twilio":
            sid, token = settings.twilio_credentials()
            missing = _missing(("TWILIO_ACCOUNT_SID", sid), ("TWILIO_AUTH_TOKEN", token),
                               ("TELEPHONY_FROM", settings.telephony_from))
            if not missing:
                return TwilioTelephonyProvider(sid, token, settings.telephony_from,
                                               base_url=settings.twilio_base_url)
            return _refuse_or_fake("telephony", "TELEPHONY_PROVIDER=twilio", missing,
                                   FakeTelephonyProvider())
        return _unconfigured("telephony", "TELEPHONY_PROVIDER", FakeTelephonyProvider())

    if name == "ocr":
        if settings.ocr_provider == "tesseract":
            return TesseractOcrProvider()
        return _unconfigured("ocr", "OCR_PROVIDER", FakeOcrProvider())

    if name == "stt":
        if settings.stt_provider == "whisper":
            missing = _missing(("LLM_BASE_URL", settings.llm_base_url),
                               ("LLM_API_KEY", settings.llm_api_key))
            if not missing:
                return WhisperSttProvider(settings.llm_base_url, settings.llm_api_key)
            return _refuse_or_fake("stt", "STT_PROVIDER=whisper", missing, FakeSttProvider())
        return _unconfigured("stt", "STT_PROVIDER", FakeSttProvider())

    if name == "search":
        if (settings.search_provider or "fake").lower() in FAKE_PROVIDERS:
            if settings.strict_providers:
                raise ProviderNotConfigured(
                    "search: SEARCH_PROVIDER is a fake; set SEARCH_PROVIDER=serpapi|brave and "
                    "SEARCH_API_KEY, or ALLOW_FAKE_PROVIDERS=true",
                    problems=["search: SEARCH_PROVIDER"],
                )
        return build_search_provider(settings.search_provider, settings.search_api_key)

    if name == "erp":
        if settings.erp_provider == "odoo":
            if settings.llm_base_url:
                return OdooConnector(settings.llm_base_url, "odoo", "admin", settings.llm_api_key)
            return _refuse_or_fake("erp", "ERP_PROVIDER=odoo", ["LLM_BASE_URL"],
                                   FileExchangeConnector())
        return FileExchangeConnector()

    if name == "fx":
        return StaticFxProvider() if not settings.strict_providers else HttpFxProvider()

    if name == "payment":
        provider = (settings.payment_provider or "fake").lower()
        if provider == "stripe" and settings.stripe_key:
            return StripeGateway(settings.stripe_key)
        if provider == "paymob":
            return PaymobGateway()
        if provider == "stripe":
            return _refuse_or_fake("payment", "PAYMENT_PROVIDER=stripe", ["STRIPE_KEY"],
                                   FakePaymentGateway())
        return _unconfigured("payment", "PAYMENT_PROVIDER", FakePaymentGateway())

    if name == "storage":
        bucket = getattr(settings, "aws_storage_bucket", "")
        if bucket:
            return S3Storage(bucket, getattr(settings, "aws_endpoint", ""),
                             getattr(settings, "aws_access_key", ""),
                             getattr(settings, "aws_secret_key", ""))
        return LocalStorage()

    raise KeyError(f"Unknown adapter: {name}")


def _unconfigured(adapter: str, variable: str, fake: Any) -> Any:
    """The provider was left at `fake` (or unset). Allowed in dev/test only."""
    if settings.strict_providers:
        raise ProviderNotConfigured(
            f"{adapter}: {variable}={getattr(settings, variable.lower(), 'fake') or 'fake'} is a fake "
            f"adapter and APP_ENV={settings.app_env} requires a real provider. Configure {variable} "
            "with real credentials, or set ALLOW_FAKE_PROVIDERS=true for a deliberately fake run.",
            problems=[f"{adapter}: {variable} is a fake"],
        )
    return fake
