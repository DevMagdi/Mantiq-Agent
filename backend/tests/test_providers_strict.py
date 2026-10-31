"""No fake adapter may survive into a real deployment.

This module is the contract behind that sentence:

* dev / test resolve the deterministic fakes (the offline pipeline keeps working);
* staging / production raise :class:`ProviderNotConfigured` with the exact variable names, in the
  adapter factory, in the LLM factory and at startup;
* a configured real provider is built — and it is the real client class, not a stand-in.
"""
from __future__ import annotations

import pytest

from app.adapters.base import registry
from app.adapters.twilio import TwilioSmsProvider, TwilioWhatsAppProvider
from app.adapters.whatsapp import CloudWhatsAppProvider, FakeWhatsAppProvider
from app.agents.llm import default_provider
from app.agents.llm.fake import FakeLLM
from app.agents.llm.providers import AnthropicProvider
from app.core.config import settings
from app.core.providers import (
    ProviderNotConfigured,
    assert_ready,
    critical_fake_selections,
    fake_selections,
    problems,
    requirements,
    warnings,
)


@pytest.fixture
def clean_settings(monkeypatch):
    """Each test starts from an unconfigured provider set and a cold adapter cache."""

    def reset() -> None:
        for name, value in {
            "app_env": "dev", "allow_fake_providers": False,
            "whatsapp_provider": "fake", "whatsapp_token": "", "whatsapp_phone_id": "",
            "sms_provider": "fake", "telephony_provider": "fake",
            "twilio_account_sid": "", "twilio_auth_token": "", "twilio_whatsapp_from": "",
            "twilio_sms_from": "", "telephony_sid": "", "telephony_token": "", "telephony_from": "",
            "llm_provider": "fake", "llm_api_key": "", "llm_base_url": "", "llm_model": "claude-sonnet-4-5",
            "email_provider": "fake", "smtp_host": "localhost", "smtp_from": "rfq@procureai.local",
            "search_provider": "fake", "search_api_key": "", "payment_provider": "fake",
            "stt_provider": "fake", "erp_provider": "file",
        }.items():
            monkeypatch.setattr(settings, name, value, raising=True)
        registry.clear_overrides()

    reset()
    yield monkeypatch
    registry.clear_overrides()


# ------------------------------------------------------------------ dev keeps the fakes
def test_dev_and_test_resolve_the_deterministic_fakes(clean_settings):
    assert settings.app_env in {"dev", "test"}
    assert isinstance(registry.resolve("whatsapp"), FakeWhatsAppProvider)
    assert isinstance(default_provider(), FakeLLM)
    assert problems() == []
    assert_ready()  # must not raise


# ------------------------------------------------------------------ staging refuses
def test_staging_refuses_a_fake_whatsapp_provider(clean_settings):
    clean_settings.setattr(settings, "app_env", "staging")

    with pytest.raises(ProviderNotConfigured) as exc:
        registry.resolve("whatsapp")

    message = str(exc.value)
    assert "WHATSAPP_PROVIDER" in message
    assert "ALLOW_FAKE_PROVIDERS" in message, "the operator must see the documented escape hatch"


def test_staging_refuses_a_fake_llm(clean_settings):
    clean_settings.setattr(settings, "app_env", "prod")

    with pytest.raises(ProviderNotConfigured, match="LLM_PROVIDER"):
        default_provider()


def test_startup_lists_every_missing_variable_in_one_message(clean_settings):
    clean_settings.setattr(settings, "app_env", "staging")
    clean_settings.setattr(settings, "whatsapp_provider", "twilio")
    clean_settings.setattr(settings, "llm_provider", "anthropic")
    clean_settings.setattr(settings, "email_provider", "smtp")
    clean_settings.setattr(settings, "smtp_host", "")

    with pytest.raises(ProviderNotConfigured) as exc:
        assert_ready()

    message = str(exc.value)
    for expected in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM",
                     "LLM_API_KEY", "SMTP_HOST"):
        assert expected in message, expected
    assert "ALLOW_FAKE_PROVIDERS=true" in message


def test_an_explicit_opt_out_allows_a_deliberately_fake_deployment(clean_settings):
    clean_settings.setattr(settings, "app_env", "staging")
    clean_settings.setattr(settings, "allow_fake_providers", True)

    assert_ready()  # no raise: the operator asked for it
    assert isinstance(registry.resolve("whatsapp"), FakeWhatsAppProvider)
    assert "WHATSAPP_PROVIDER=fake" in fake_selections()
    assert "WHATSAPP_PROVIDER=fake" in critical_fake_selections()
    assert settings.strict_providers is False
    assert any("SEARCH_PROVIDER" in entry for entry in warnings())


# ------------------------------------------------------------------ real providers build
def test_a_configured_twilio_account_builds_the_real_clients(clean_settings):
    clean_settings.setattr(settings, "app_env", "staging")
    clean_settings.setattr(settings, "whatsapp_provider", "twilio")
    clean_settings.setattr(settings, "sms_provider", "twilio")
    clean_settings.setattr(settings, "twilio_account_sid", "AC123")
    clean_settings.setattr(settings, "twilio_auth_token", "token")
    clean_settings.setattr(settings, "twilio_whatsapp_from", "whatsapp:+14155238886")
    clean_settings.setattr(settings, "twilio_sms_from", "+14155238886")
    clean_settings.setattr(settings, "llm_provider", "anthropic")
    clean_settings.setattr(settings, "llm_api_key", "sk-ant-test")
    # Email is critical too: a deployment that cannot send email cannot dispatch an RFQ.
    clean_settings.setattr(settings, "email_provider", "smtp")
    clean_settings.setattr(settings, "smtp_host", "smtp.example.com")
    clean_settings.setattr(settings, "smtp_from", "rfq@example.com")
    # …and so are the voice providers, because AI calls are enabled by default.
    clean_settings.setattr(settings, "telephony_provider", "twilio")
    clean_settings.setattr(settings, "telephony_from", "+14155238886")
    clean_settings.setattr(settings, "stt_provider", "whisper")
    clean_settings.setattr(settings, "llm_base_url", "https://api.openai.com/v1")

    whatsapp = registry.resolve("whatsapp")
    sms = registry.resolve("sms")
    llm = default_provider()

    assert isinstance(whatsapp, TwilioWhatsAppProvider)
    assert isinstance(sms, TwilioSmsProvider)
    assert isinstance(llm, AnthropicProvider)
    assert registry.resolve("telephony").name == "twilio"
    assert problems() == []
    assert_ready()  # production-ready: nothing critical missing, nothing critical faked
    # Optional capabilities may stay faked, but they are reported, never silently ignored.
    assert critical_fake_selections() == []


def test_the_meta_cloud_provider_still_needs_its_own_credentials(clean_settings):
    clean_settings.setattr(settings, "app_env", "staging")
    clean_settings.setattr(settings, "whatsapp_provider", "cloud_api")

    with pytest.raises(ProviderNotConfigured, match="WHATSAPP_TOKEN"):
        registry.resolve("whatsapp")

    clean_settings.setattr(settings, "whatsapp_token", "EAAG")
    clean_settings.setattr(settings, "whatsapp_phone_id", "123456")
    assert isinstance(registry.resolve("whatsapp"), CloudWhatsAppProvider)


def test_the_legacy_telephony_variables_still_configure_twilio(clean_settings):
    clean_settings.setattr(settings, "app_env", "staging")
    clean_settings.setattr(settings, "whatsapp_provider", "twilio")
    clean_settings.setattr(settings, "telephony_sid", "AC-legacy")
    clean_settings.setattr(settings, "telephony_token", "legacy-token")
    clean_settings.setattr(settings, "twilio_whatsapp_from", "whatsapp:+14155238886")

    provider = registry.resolve("whatsapp")

    assert isinstance(provider, TwilioWhatsAppProvider)
    assert provider.account_sid == "AC-legacy"


# ------------------------------------------------------------------ the requirement table
def test_requirements_name_the_variables_for_every_selected_provider(clean_settings):
    clean_settings.setattr(settings, "whatsapp_provider", "twilio")
    clean_settings.setattr(settings, "llm_provider", "openai_compatible")
    clean_settings.setattr(settings, "email_provider", "smtp")

    table = {requirement.adapter: requirement.env for requirement in requirements()}

    assert set(table["whatsapp"]) == {"TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM"}
    assert set(table["llm"]) == {"LLM_API_KEY", "LLM_BASE_URL"}
    assert table["email"] == ("SMTP_HOST",)
