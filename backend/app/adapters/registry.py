"""Adapter factory: environment-driven, with Fake as the safe default everywhere."""
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
from app.adapters.whatsapp import CloudWhatsAppProvider, FakeWhatsAppProvider
from app.core.config import settings


def build(name: str) -> Any:
    if name == "email":
        if settings.email_provider == "smtp" and settings.smtp_host:
            return SmtpEmailProvider(settings.smtp_host, settings.smtp_port, settings.smtp_user,
                                     settings.smtp_password, settings.smtp_from)
        return FakeEmailProvider()

    if name == "whatsapp":
        if settings.whatsapp_provider == "cloud_api" and settings.whatsapp_token:
            return CloudWhatsAppProvider(settings.whatsapp_token, settings.whatsapp_phone_id,
                                         settings.whatsapp_verify_token)
        return FakeWhatsAppProvider(settings.whatsapp_verify_token)

    if name == "telephony":
        if settings.telephony_provider == "twilio" and settings.telephony_sid:
            return TwilioTelephonyProvider(settings.telephony_sid, settings.telephony_token,
                                           settings.telephony_from)
        return FakeTelephonyProvider()

    if name == "ocr":
        if settings.ocr_provider == "tesseract":
            return TesseractOcrProvider()
        return FakeOcrProvider()

    if name == "stt":
        if settings.stt_provider == "whisper" and settings.llm_base_url:
            return WhisperSttProvider(settings.llm_base_url, settings.llm_api_key)
        return FakeSttProvider()

    if name == "search":
        return build_search_provider(settings.search_provider, settings.search_api_key)

    if name == "erp":
        if settings.erp_provider == "odoo" and settings.llm_base_url:
            return OdooConnector(settings.llm_base_url, "odoo", "admin", settings.llm_api_key)
        return FileExchangeConnector()

    if name == "fx":
        return StaticFxProvider() if settings.app_env != "prod" else HttpFxProvider()

    if name == "payment":
        if settings.payment_provider == "stripe" and settings.stripe_key:
            return StripeGateway(settings.stripe_key)
        if settings.payment_provider == "paymob":
            return PaymobGateway()
        return FakePaymentGateway()

    if name == "storage":
        bucket = getattr(settings, "aws_storage_bucket", "")
        if bucket:
            return S3Storage(bucket, getattr(settings, "aws_endpoint", ""),
                             getattr(settings, "aws_access_key", ""),
                             getattr(settings, "aws_secret_key", ""))
        return LocalStorage()

    raise KeyError(f"Unknown adapter: {name}")
