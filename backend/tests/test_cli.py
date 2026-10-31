"""Operator CLI contract (Section 15): every command must work against a real database."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


def _json(output: str) -> dict:
    return json.loads(output)


def test_doctor_reports_configuration():
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    body = _json(result.output)
    assert body["app_env"] == "test"
    assert body["database"] == "sqlite+aiosqlite"
    assert "llm_provider" in body


def test_bootstrap_is_idempotent():
    result = runner.invoke(app, ["bootstrap", "--demo"])
    assert result.exit_code == 0, result.output
    body = _json(result.output)
    assert body.get("platform_tenant") or body.get("tenants")


def test_create_tenant_and_user_then_reset_password():
    created = runner.invoke(app, ["create-tenant", "CLI Tenant", "cli-tenant", "--plan", "starter"])
    assert created.exit_code == 0, created.output
    assert _json(created.output)["slug"] == "cli-tenant"

    user = runner.invoke(app, ["create-user", "cli-tenant", "ops@cli-tenant.example", "--role", "buyer"])
    assert user.exit_code == 0, user.output
    payload = _json(user.output)
    assert payload["email"] == "ops@cli-tenant.example"
    assert payload["password"] not in {"", "(set)"}

    weak = runner.invoke(app, ["create-user", "cli-tenant", "weak@cli-tenant.example", "--password", "password"])
    assert weak.exit_code != 0

    unknown = runner.invoke(app, ["create-user", "does-not-exist", "x@y.example"])
    assert unknown.exit_code != 0

    reset = runner.invoke(app, ["reset-password", "cli-tenant", "ops@cli-tenant.example"])
    assert reset.exit_code == 0, reset.output
    assert _json(reset.output)["password"]

    missing = runner.invoke(app, ["reset-password", "cli-tenant", "ghost@cli-tenant.example"])
    assert missing.exit_code != 0


def test_seed_demo_and_fx_refresh_and_purge():
    seeded = runner.invoke(app, ["seed-demo", "--slug", "nile"])
    assert seeded.exit_code == 0, seeded.output
    assert _json(seeded.output)

    fx = runner.invoke(app, ["fx-refresh"])
    assert fx.exit_code == 0, fx.output
    rates = _json(fx.output)
    assert "USD/EGP" in rates and float(rates["USD/EGP"]) > 0

    purged = runner.invoke(app, ["purge"])
    assert purged.exit_code == 0, purged.output
    assert "nile" in _json(purged.output)


def test_migrate_head_is_a_noop_when_current():
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 0
    assert "migrated to head" in result.output


def test_help_lists_every_documented_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("migrate", "bootstrap", "seed-demo", "create-tenant", "create-user",
                    "reset-password", "purge", "fx-refresh", "doctor"):
        assert command in result.output


def test_doctor_is_a_go_live_gate_and_never_prints_a_secret(monkeypatch):
    """`doctor` is the first command the runbook tells an operator to run: it must be real."""
    from app.core.config import settings

    ok = runner.invoke(app, ["doctor"])
    assert ok.exit_code == 0, ok.output
    body = _json(ok.output)
    assert body["database_check"]["connectivity"] == "ok"
    assert body["database_check"]["migration_head"]
    names = {row["name"] for row in body["providers"]}
    assert {"whatsapp", "sms", "telephony", "llm", "email"} <= names
    assert body["ws_fanout_enabled"] is True
    # A secret value must never appear in the report — only whether the variable is set.
    for secret in filter(None, [settings.secret_key, settings.llm_api_key,
                                settings.twilio_auth_token, settings.encryption_master_key]):
        assert secret not in ok.output

    monkeypatch.setattr(settings, "app_env", "production")
    blocked = runner.invoke(app, ["doctor"])
    assert blocked.exit_code == 1, blocked.output
    assert _json(blocked.output)["critical_fakes"]


def test_doctor_reports_a_dead_database_instead_of_crashing(monkeypatch):
    """A report that dies with the database is useless exactly when it is needed most."""
    import app.cli as cli_module

    class BrokenSession:
        async def __aenter__(self):
            raise OSError("connection refused")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(cli_module, "SessionLocal", BrokenSession)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert _json(result.output)["database_check"]["connectivity"] == "failed: OSError"


def test_doctor_checks_postgres_extensions_only_when_the_url_is_postgres(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "database_url",
                        "postgresql+asyncpg://procureai:change-me@db:5432/procureai")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    body = _json(result.output)
    assert body["database"] == "postgresql+asyncpg"
    # The test database is SQLite, so the extension catalogue is unreachable — and that is reported.
    assert body["database_check"]["connectivity"].startswith("failed:")
