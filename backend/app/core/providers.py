"""Provider configuration policy — no fake adapter may survive into a real deployment.

The adapters are chosen from environment variables (``WHATSAPP_PROVIDER``, ``LLM_PROVIDER``, …). When
the chosen provider is not configured, dev and test fall back to a deterministic fake so the whole
pipeline runs offline; **staging and production must not**, because a silent fake means a supplier
never receives an RFQ and nobody notices until the money is missing.

Two entry points:

* :func:`requirements` — the declarative table of "if this provider is selected, these variables must
  be set". It is the single source of truth used by the registry, the LLM factory, the CLI ``doctor``
  and the startup check, so an operator sees exactly one answer everywhere.
* :func:`assert_ready` — called from the application lifespan. In strict mode it raises
  :class:`ProviderNotConfigured` listing every missing variable with the provider it belongs to.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, settings


class ProviderNotConfigured(RuntimeError):
    """Raised when a real deployment selects a provider it has no credentials for."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = problems or []


@dataclass(frozen=True)
class Requirement:
    adapter: str
    selected_by: str
    env: tuple[str, ...]
    note: str = ""

    def missing(self, config: Settings) -> list[str]:
        values = {name: getattr(config, name.lower(), "") for name in self.env}
        return [name for name, value in values.items() if not str(value or "").strip()]


FAKE_PROVIDERS = {"", "fake", "none", "stub", "mock"}


def requirements(config: Settings | None = None) -> list[Requirement]:
    """Everything the *selected* providers need, in the order an operator should set it."""
    config = config or settings
    reqs: list[Requirement] = []

    whatsapp = (config.whatsapp_provider or "fake").lower()
    if whatsapp == "cloud_api":
        # APP_SECRET is a hard requirement, not a footnote: without it the Cloud adapter rejects
        # every webhook (Meta signs each delivery), so the endpoint would answer 401 to real traffic
        # with nothing in the logs to say why. Refusing to boot is the honest failure mode.
        reqs.append(Requirement("whatsapp", "WHATSAPP_PROVIDER=cloud_api",
                                ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_APP_SECRET"),
                                "the app secret verifies every webhook delivery (X-Hub-Signature-256)"))
    elif whatsapp == "twilio":
        reqs.append(Requirement("whatsapp", "WHATSAPP_PROVIDER=twilio",
                                ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM"),
                                "the from number must be the WhatsApp-enabled sender, e.g. whatsapp:+1415…"))

    if (config.sms_provider or "fake").lower() == "twilio":
        reqs.append(Requirement("sms", "SMS_PROVIDER=twilio",
                                ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_SMS_FROM")))

    if (config.telephony_provider or "fake").lower() == "twilio":
        reqs.append(Requirement("telephony", "TELEPHONY_PROVIDER=twilio",
                                ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TELEPHONY_FROM")))

    llm = (config.llm_provider or "fake").lower()
    if llm == "anthropic":
        reqs.append(Requirement("llm", "LLM_PROVIDER=anthropic", ("LLM_API_KEY",),
                                "LLM_MODEL selects the model (default claude-sonnet-4-5)"))
    elif llm in {"openai", "openai_compatible", "self_hosted"}:
        reqs.append(Requirement("llm", f"LLM_PROVIDER={llm}", ("LLM_API_KEY", "LLM_BASE_URL"),
                                "any OpenAI-compatible endpoint: vLLM, Ollama, Azure, OpenAI"))

    if (config.email_provider or "fake").lower() == "smtp":
        reqs.append(Requirement("email", "EMAIL_PROVIDER=smtp", ("SMTP_HOST",)))

    if (config.search_provider or "fake").lower() in {"serpapi", "brave"}:
        reqs.append(Requirement("search", f"SEARCH_PROVIDER={config.search_provider}",
                                ("SEARCH_API_KEY",)))

    if (config.stt_provider or "fake").lower() == "whisper":
        reqs.append(Requirement("stt", "STT_PROVIDER=whisper", ("LLM_BASE_URL", "LLM_API_KEY")))

    if (config.erp_provider or "fake").lower() == "odoo":
        reqs.append(Requirement("erp", "ERP_PROVIDER=odoo", ("LLM_BASE_URL",)))

    if (config.payment_provider or "fake").lower() == "stripe":
        reqs.append(Requirement("payment", "PAYMENT_PROVIDER=stripe", ("STRIPE_KEY",)))
    return reqs


def _selected(config: Settings) -> dict[str, str]:
    return {
        "WHATSAPP_PROVIDER": config.whatsapp_provider,
        "SMS_PROVIDER": config.sms_provider,
        "TELEPHONY_PROVIDER": config.telephony_provider,
        "LLM_PROVIDER": config.llm_provider,
        "EMAIL_PROVIDER": config.email_provider,
        "SEARCH_PROVIDER": config.search_provider,
        "STT_PROVIDER": config.stt_provider,
        "OCR_PROVIDER": config.ocr_provider,
    }


def _is_critical(name: str, config: Settings) -> bool:
    """Is this provider on the path of a feature the tenant actually uses?

    A fake supplier-discovery provider is harmless when nobody asked for discovery; a fake email or
    LLM provider is not, because e-mail dispatch and drafting are part of every RFQ. Voice providers
    become critical only when the AI-voice feature is switched on.
    """
    if name == "EMAIL_PROVIDER":
        return True          # every RFQ has an email leg; the platform cannot be silent here
    if name == "LLM_PROVIDER":
        return True          # drafting, extraction assist, negotiation text, assistant
    if name == "WHATSAPP_PROVIDER":
        return bool(config.whatsapp_enabled)
    if name in {"TELEPHONY_PROVIDER", "STT_PROVIDER"}:
        return bool(config.voice_ai_enabled)
    return False             # SMS, search, OCR, payment, ERP are opt-in capabilities


def fake_selections(config: Settings | None = None) -> list[str]:
    """Providers still pointing at a fake while the environment demands the real thing."""
    config = config or settings
    return [f"{name}={value or 'fake'}" for name, value in _selected(config).items()
            if (value or "").lower() in FAKE_PROVIDERS]


def critical_fake_selections(config: Settings | None = None) -> list[str]:
    """The fakes that would actually break a working feature in this deployment."""
    config = config or settings
    return [entry for entry in fake_selections(config)
            if _is_critical(entry.split("=", 1)[0], config)]


def warnings(config: Settings | None = None) -> list[str]:
    """Optional capabilities still on a fake — logged, not fatal."""
    config = config or settings
    critical = set(critical_fake_selections(config))
    return [f"{entry} (optional capability disabled or faked)" for entry in fake_selections(config)
            if entry not in critical]


def problems(config: Settings | None = None) -> list[str]:
    """Human-readable list of everything blocking a real deployment (empty when ready)."""
    config = config or settings
    found: list[str] = []
    for requirement in requirements(config):
        missing = requirement.missing(config)
        if missing:
            hint = f" ({requirement.note})" if requirement.note else ""
            found.append(f"{requirement.adapter}: {requirement.selected_by} requires "
                         f"{', '.join(missing)}{hint}")
    return found


def assert_ready(config: Settings | None = None) -> None:
    """Fail fast at startup when a real deployment is missing credentials.

    Dev and test return immediately: the deterministic fakes are the point there. Staging with
    ``ALLOW_FAKE_PROVIDERS=true`` logs loudly instead (explicit, visible, reversible) — that is the
    only way to boot a staging box before the provider contracts are signed.
    """
    config = config or settings
    found = problems(config)
    if not config.strict_providers:
        return
    fakes = critical_fake_selections(config)
    if fakes:
        found.append("these providers are still set to a fake: " + ", ".join(sorted(fakes)))
    if found:
        raise ProviderNotConfigured(
            "Provider configuration is incomplete for APP_ENV="
            f"{config.app_env}. Fix the environment variables below, or set "
            "ALLOW_FAKE_PROVIDERS=true to run a deliberately fake staging box.\n  - "
            + "\n  - ".join(found),
            problems=found,
        )
