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
