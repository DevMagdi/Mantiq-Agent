"""Test harness: isolated SQLite database, real migrations, seeded tenant, ASGI client.

The whole suite exercises the real application object: real routers, real services, real policy
engine. Only the outbound adapters are the built-in deterministic fakes (see app/adapters).
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="procureai-tests-"))
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": f"sqlite+aiosqlite:///{_TMP / 'test.db'}",
    "LLM_PROVIDER": "fake",
    "SECRET_KEY": "test-secret-key-not-for-production-0123456789abcdef",
    "BOOTSTRAP_ON_START": "false",
    "RATE_LIMIT_ENABLED": "false",
    "PUBLIC_BASE_URL": "http://testserver",
    # The suite must not depend on when it runs: the contact-hours rule has its own
    # explicit-clock tests (tests/test_policy_engine.py, tests/test_services_negotiation.py).
    "CONTACT_HOURS_START": "0",
    "CONTACT_HOURS_END": "23",
})

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db.models.entities import Tenant, User  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.services.bootstrap import bootstrap  # noqa: E402
from app.services.migrations import upgrade_sync  # noqa: E402
from app.services.seed import seed_demo  # noqa: E402

DEMO_PASSWORD = "Demo!Pilot2026"


def pytest_configure(config):  # noqa: D103
    config.addinivalue_line("markers", "slow: long running integration scenarios")


async def _prepare_database() -> None:
    upgrade_sync()
    async with SessionLocal() as session:
        await bootstrap(session, force=False, demo=True)
        await session.commit()


@pytest.fixture(scope="session", autouse=True)
def prepared_database():
    upgrade_sync()
    asyncio.run(_bootstrap_and_seed())
    return True


async def _bootstrap_and_seed() -> None:
    async with SessionLocal() as session:
        result = await bootstrap(session, force=False, demo=True)
        await session.commit()
        if not result.get("demo_seeded"):
            tenants = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalars().all()
            for tenant in tenants:
                await seed_demo(session, tenant, demo_password=DEMO_PASSWORD)
            await session.commit()


@pytest_asyncio.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _login(client: httpx.AsyncClient, email: str) -> dict:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def admin(client) -> dict:
    data = await _login(client, "admin@pilot.procureai")
    return {"token": data["access_token"], "user": data["user"],
            "headers": {"authorization": f"Bearer {data['access_token']}"}}


@pytest_asyncio.fixture
async def manager(client) -> dict:
    data = await _login(client, "manager@nile.example.com")
    return {"token": data["access_token"], "user": data["user"],
            "headers": {"authorization": f"Bearer {data['access_token']}"}}


@pytest_asyncio.fixture
async def buyer(client) -> dict:
    data = await _login(client, "buyer@nile.example.com")
    return {"token": data["access_token"], "user": data["user"],
            "headers": {"authorization": f"Bearer {data['access_token']}"}}


@pytest_asyncio.fixture
async def requester(client) -> dict:
    data = await _login(client, "requester@nile.example.com")
    return {"token": data["access_token"], "user": data["user"],
            "headers": {"authorization": f"Bearer {data['access_token']}"}}


@pytest_asyncio.fixture
async def finance(client) -> dict:
    data = await _login(client, "finance@nile.example.com")
    return {"token": data["access_token"], "user": data["user"],
            "headers": {"authorization": f"Bearer {data['access_token']}"}}


@pytest_asyncio.fixture
async def warehouse(client) -> dict:
    data = await _login(client, "storekeeper@nile.example.com")
    return {"token": data["access_token"], "user": data["user"],
            "headers": {"authorization": f"Bearer {data['access_token']}"}}


@pytest_asyncio.fixture
async def second_tenant() -> Tenant:
    """A second tenant used by isolation tests."""
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "isolation-co"))).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name="Isolation Co", slug="isolation-co", plan_code="growth", status="active",
                            currency="EGP", timezone="Africa/Cairo", settings={})
            session.add(tenant)
            await session.flush()
            user = User(tenant_id=tenant.id, email="owner@isolation.example.com",
                        full_name="Isolation Owner", primary_role="admin", roles=["admin"],
                        is_superadmin=False, is_active=True, hashed_password="")
            session.add(user)
            await session.commit()
            from app.core.security import hash_password

            user.hashed_password = hash_password(DEMO_PASSWORD)
            await session.commit()
        return tenant


def unique(prefix: str = "t") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
