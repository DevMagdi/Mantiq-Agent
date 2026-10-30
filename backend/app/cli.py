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

    async def _inner():
        async with SessionLocal() as session:
            report = await run_bootstrap(session, force=True, demo=demo)
            return report

    report = _run(_inner())
    typer.echo(json.dumps(report, indent=2, default=str))


@app.command("seed-demo")
def seed_demo(slug: str = typer.Option("nile", help="Tenant slug to seed")):
    """Load the Egyptian-market demo dataset (never run in production)."""
    if settings.is_prod:
        typer.echo("Refusing to seed demo data in production.", err=True)
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
    """Print environment/config health hints."""
    typer.echo(json.dumps({
        "app_env": settings.app_env,
        "database": settings.database_url.split("://")[0],
        "llm_provider": settings.llm_provider,
        "email_provider": settings.email_provider,
        "whatsapp_provider": settings.whatsapp_provider,
        "telephony_provider": settings.telephony_provider,
        "search_provider": settings.search_provider,
        "bootstrap_on_start": settings.bootstrap_on_start,
        "public_url": settings.public_app_url,
        "note": "Adapters left as 'fake' run in sandbox mode with deterministic data.",
    }, indent=2))


if __name__ == "__main__":
    app()
