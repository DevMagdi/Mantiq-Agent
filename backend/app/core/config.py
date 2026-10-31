"""Application configuration (12-factor, env driven)."""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore", case_sensitive=False)

    # --- core ---
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 240
    # Unauthenticated traffic is bucketed per client IP. The default is deliberately generous: one
    # corporate NAT (a whole office, or a load balancer) can legitimately produce hundreds of
    # requests a minute, and a per-IP cap that throttles a tenant is a bug, not a defence.
    rate_limit_anonymous_per_minute: int = 600
    # Sign-in is bucketed twice: per account (credential stuffing against one user) and per IP
    # (mass attempts from one host) — the per-IP cap allows a full office to arrive at 08:00.
    rate_limit_login_per_account: int = 10
    rate_limit_login_per_ip: int = 120
    public_base_url: str = "http://localhost:5173"
    app_name: str = "ProcureAI"
    app_env: str = "dev"  # dev | staging | prod
    api_prefix: str = "/api/v1"
    secret_key: str = ""
    access_token_ttl_minutes: int = 60
    refresh_token_ttl_days: int = 14
    require_mfa: bool = False

    # --- data ---
    # PostgreSQL 16 is the supported database. SQLite exists only for the test suite and for
    # sandboxes without a PostgreSQL server, and must be opted into explicitly.
    database_url: str = "postgresql+asyncpg://procureai:change-me@localhost:5432/procureai"
    allow_sqlite_fallback: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800
    db_statement_timeout_ms: int = 15000
    redis_url: str = "redis://localhost:6379/0"
    tenant_rls_enabled: bool = True
    demo_seed_on_start: bool = True

    # --- bootstrap (Section 5) ---
    bootstrap_on_start: bool = True
    bootstrap_demo_tenant: bool = True
    bootstrap_superadmin_email: str = "owner@mantiq.tech"
    bootstrap_superadmin_password: str = ""
    bootstrap_admin_email: str = "admin@pilot.procureai"
    bootstrap_admin_password: str = ""
    bootstrap_demo_password: str = "Demo!Pilot2026"

    # --- external providers (all optional: Fake adapters are the default) ---
    llm_provider: str = "fake"  # fake | anthropic | openai_compatible
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-4-5"
    llm_base_url: str = ""
    search_provider: str = "fake"  # fake | serpapi | brave
    search_api_key: str = ""
    email_provider: str = "fake"  # fake | smtp
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "rfq@procureai.local"
    whatsapp_provider: str = "fake"  # fake | cloud_api
    whatsapp_token: str = ""
    whatsapp_phone_id: str = ""
    whatsapp_verify_token: str = "verify-me"
    whatsapp_app_secret: str = ""
    # A published WhatsApp Flow id gives suppliers a structured quote form; without one the
    # provider falls back to an approved template that asks for the same three numbers.
    whatsapp_quote_flow_id: str = ""
    # Phase 3 channel + AI-voice master switches. Both layers must agree: this global switch AND
    # the tenant feature flag (tenant.settings["features"]) before a channel or AI feature is used.
    whatsapp_enabled: bool = True
    voice_ai_enabled: bool = True
    voice_assistant_enabled: bool = True
    whatsapp_daily_cap: int = 400
    whatsapp_session_window_hours: int = 24
    telephony_provider: str = "fake"  # fake | twilio
    telephony_sid: str = ""
    telephony_token: str = ""
    telephony_from: str = ""
    ocr_provider: str = "fake"  # fake | tesseract
    stt_provider: str = "fake"  # fake | whisper
    payment_provider: str = "fake"  # fake | stripe
    stripe_key: str = ""
    erp_provider: str = "file"  # file | odoo

    # --- security ---
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_upload_mb: int = 20
    encryption_master_key: str = ""
    aws_storage_bucket: str = ""
    aws_endpoint: str = ""
    aws_access_key: str = ""
    aws_secret_key: str = ""

    # --- misc ---
    public_app_url: str = "http://localhost:5173"
    default_timezone: str = "Africa/Cairo"
    # Outbound supplier contact window, local to the tenant timezone. The policy engine blocks
    # autonomous sends outside it; tests widen it so the suite never depends on the wall clock.
    contact_hours_start: int = 7
    contact_hours_end: int = 21
    sentry_dsn: str = ""
    otel_enabled: bool = False

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def resolved_secret(self) -> str:
        if self.secret_key:
            return self.secret_key
        # Dev fallback: ephemeral per-process key (sessions do not survive restart).
        return secrets.token_urlsafe(48)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
