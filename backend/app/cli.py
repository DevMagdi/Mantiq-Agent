"""ProcureAI operator CLI (typer): migrate, bootstrap, seed-demo, create-tenant, create-user, ..."""
from __future__ import annotations

import asyncio
import json

import typer
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password, password_policy_errors, random_password
from app.db.models.entities import Tenant, User
from app.db.session import SessionLocal
from app.services.bootstrap import bootstrap as run_bootstrap
from app.services.bootstrap import ensure_tenant, ensure_user, seed_reference_data, seed_tenant_defaults
from app.services.migrations import upgrade_sync
from app.services.seed import seed_demo_tenant as run_seed_tenant

app = typer.Typer(help="ProcureAI operations CLI", no_args_is_help=True)


def _run(coro):
    return asyncio.run(coro)


@app.command()
def migrate(revision: str = typer.Argument("head")):
    """Apply Alembic migrations (upgrade)."""
    upgrade_sync(revision)
    typer.echo(f"migrated to {revision}")


@app.command()
def bootstrap(demo: bool = typer.Option(True, help="Also create the demo customer tenant")):
    """Create the platform tenant, SuperAdmin and (optionally) the demo tenant + Admin. Idempotent."""

    if demo and settings.demo_refusal():
        typer.echo(f"{settings.demo_refusal()}: rerun with --no-demo to bootstrap only the platform.",
                   err=True)
        raise typer.Exit(code=1)

    async def _inner():
        async with SessionLocal() as session:
            report = await run_bootstrap(session, force=True, demo=demo)
            return report

    report = _run(_inner())
    typer.echo(json.dumps(report, indent=2, default=str))


@app.command("seed-demo")
def seed_demo(slug: str = typer.Option("nile", help="Tenant slug to seed")):
    """Load the Egyptian-market demo dataset (never run in staging or production)."""
    if settings.demo_refusal():
        typer.echo(f"{settings.demo_refusal()}.", err=True)
        raise typer.Exit(code=1)

    async def _inner():
        async with SessionLocal() as session:
            result = await run_seed_tenant(
                session, slug, demo_password=settings.bootstrap_demo_password
            )
            await session.commit()
            return result

    typer.echo(json.dumps(_run(_inner()), indent=2, default=str))


@app.command("create-tenant")
def create_tenant(name: str, slug: str, plan: str = "growth", locale: str = "ar"):
    """Create a customer tenant with default categories, templates and approval chain."""

    async def _inner():
        async with SessionLocal() as session:
            await seed_reference_data(session)
            tenant = await ensure_tenant(session, name=name, slug=slug, plan_code=plan, locale=locale)
            await seed_tenant_defaults(session, tenant)
            await session.commit()
            return {"id": tenant.id, "slug": tenant.slug, "name": tenant.name}

    typer.echo(json.dumps(_run(_inner()), indent=2))


@app.command("create-user")
def create_user(
    tenant_slug: str,
    email: str,
    role: str = "buyer",
    password: str = typer.Option("", help="Omit to generate a strong password"),
    name: str = "",
):
    """Create a user inside a tenant (forces password change on first login)."""

    async def _inner():
        async with SessionLocal() as session:
            tenant = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one_or_none()
            if not tenant:
                raise typer.BadParameter(f"Unknown tenant slug: {tenant_slug}")
            pwd = password or random_password()
            errors = password_policy_errors(pwd, email)
            if errors:
                raise typer.BadParameter("; ".join(errors))
            user, created = await ensure_user(session, tenant=tenant, email=email, password=pwd,
                                              full_name=name or email.split("@")[0], full_name_ar="",
                                              roles=[role])
            await session.commit()
            return {"id": user.id, "email": user.email, "created": created, "password": pwd if not password else "(set)"}

    typer.echo(json.dumps(_run(_inner()), indent=2))


@app.command("reset-password")
def reset_password(tenant_slug: str, email: str, password: str = ""):
    """Reset a user's password and force a change at next login."""

    async def _inner():
        async with SessionLocal() as session:
            tenant = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one_or_none()
            if not tenant:
                raise typer.BadParameter("Unknown tenant")
            user = (await session.execute(
                select(User).where(User.tenant_id == tenant.id, User.email == email.lower())
            )).scalar_one_or_none()
            if not user:
                raise typer.BadParameter("Unknown user")
            pwd = password or random_password()
            user.hashed_password = hash_password(pwd)
            user.must_change_password = True
            user.failed_logins = 0
            user.locked_until = None
            await session.commit()
            return {"email": user.email, "password": pwd}

    typer.echo(json.dumps(_run(_inner()), indent=2))


@app.command()
def purge():
    """Run retention purges and pending erasure requests for every tenant."""

    async def _inner():
        from app.services.billing_helpers import purge_expired

        results = {}
        async with SessionLocal() as session:
            tenants = list((await session.execute(select(Tenant))).scalars().all())
            for tenant in tenants:
                results[tenant.slug] = await purge_expired(session, tenant)
            await session.commit()
        return results

    typer.echo(json.dumps(_run(_inner()), indent=2, default=str))


@app.command("fx-refresh")
def fx_refresh():
    """Warm the FX cache for the supported currency pairs."""

    async def _inner():
        from app.services.common import get_fx_rate

        pairs = [("USD", "EGP"), ("EUR", "EGP"), ("SAR", "EGP"), ("AED", "EGP"), ("CNY", "EGP")]
        out = {}
        async with SessionLocal() as session:
            for base, quote in pairs:
                out[f"{base}/{quote}"] = str(await get_fx_rate(session, base, quote))
            await session.commit()
        return out

    typer.echo(json.dumps(_run(_inner()), indent=2))


@app.command()
def doctor():
    """Report what this process actually resolved: database, extensions, adapters, live events.

    Prints no secret values — only whether a variable is set. Exits non-zero when a strict
    deployment (staging/production) still has an unconfigured or faked critical provider, so the
    command is usable as a go-live gate in the runbook and in CI.
    """
    from sqlalchemy import text

    from app.core import providers

    db: dict[str, object] = {"driver": settings.database_url.split("://")[0]}

    async def _check() -> dict[str, object]:
        try:
            async with SessionLocal() as session:
                await session.execute(text("SELECT 1"))
                db["connectivity"] = "ok"
                try:
                    head = (await session.execute(
                        text("SELECT version_num FROM alembic_version")
                    )).scalar_one_or_none()
                    db["migration_head"] = head or "none"
                except Exception:  # noqa: BLE001 - a fresh database has no alembic table yet
                    db["migration_head"] = "none"
                if "postgresql" in settings.database_url:
                    rows = (await session.execute(text(
                        "SELECT extname FROM pg_extension WHERE extname IN "
                        "('vector', 'pg_trgm', 'citext', 'pgcrypto') ORDER BY extname"
                    ))).scalars().all()
                    db["extensions"] = list(rows)
        except Exception as exc:  # noqa: BLE001 - the report must survive an unreachable database
            db["connectivity"] = f"failed: {type(exc).__name__}"
        return db

    _run(_check())

    selected = {
        "whatsapp": settings.whatsapp_provider,
        "sms": settings.sms_provider,
        "telephony": settings.telephony_provider,
        "llm": settings.llm_provider,
        "email": settings.email_provider,
        "search": settings.search_provider,
        "stt": settings.stt_provider,
        "ocr": settings.ocr_provider,
        "erp": settings.erp_provider,
        "payment": settings.payment_provider,
    }
    fakes = providers.fake_selections(settings)
    critical_fakes = providers.critical_fake_selections(settings)
    problems = providers.problems(settings)
    critical_names = {entry.split("=", 1)[0] for entry in critical_fakes}
    payload = {
        "app_env": settings.app_env,
        "strict_providers": settings.strict_providers,
        "allow_fake_providers": settings.allow_fake_providers,
        "database": db["driver"],
        "database_check": db,
        "tenant_rls_enabled": settings.tenant_rls_enabled,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "providers": [
            {"name": name, "selected": value or "fake",
             "mode": "fake" if (value or "").lower() in providers.FAKE_PROVIDERS else "real",
             "critical": f"{name.upper()}_PROVIDER" in critical_names}
            for name, value in selected.items()
        ],
        "fakes": fakes,
        "critical_fakes": critical_fakes,
        "missing_credentials": problems,
        "ws_fanout_enabled": settings.ws_fanout_enabled,
        "redis_configured": bool(settings.redis_url),
        "bootstrap_on_start": settings.bootstrap_on_start,
        "public_url": settings.public_app_url,
        "note": ("Every adapter is real." if not fakes else
                 "Adapters listed under 'fakes' run in sandbox mode with deterministic data."),
    }
    typer.echo(json.dumps(payload, indent=2, default=str))
    if settings.strict_providers and (problems or critical_fakes):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
