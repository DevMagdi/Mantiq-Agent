"""Embeddings are a real HTTP call when configured, and absent when they are not.

The local server speaks `/v1/embeddings`. Nothing in the application fabricates a vector.
"""
from __future__ import annotations

import contextlib
import socket

import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select

from app.core.config import settings
from app.db.models.entities import Supplier, Tenant
from app.db.session import SessionLocal
from app.services.suppliers import create_supplier, search_suppliers


def _vector_for(text: str) -> list[float]:
    # The query token is not a substring of any supplier name, so the lexical filter misses
    # and only the stored vector can find the row. Two axes are enough to prove cosine.
    if "rebar" in text.lower() or text.strip() == "semantic-query-token":
        return [1.0, 0.0, 0.0, 0.0]
    return [0.0, 1.0, 0.0, 0.0]


class Embeddings:
    def __init__(self) -> None:
        self.calls = 0
        self.base = ""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.asynccontextmanager
async def embed_server(vendor: Embeddings):
    import uvicorn

    app = FastAPI()

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        payload = await request.json()
        vendor.calls += 1
        inputs = payload.get("input")
        if isinstance(inputs, str):
            inputs = [inputs]
        data = [{"index": index, "embedding": _vector_for(str(text))} for index, text in enumerate(inputs or [])]
        return {"data": data, "model": payload.get("model")}

    port = _free_port()
    vendor.base = f"http://127.0.0.1:{port}/v1"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    import asyncio

    task = asyncio.create_task(server.serve())
    try:
        for _ in range(50):
            if server.started:
                break
            await asyncio.sleep(0.05)
        yield
    finally:
        server.should_exit = True
        await task


def _enable(monkeypatch, vendor: Embeddings) -> None:
    monkeypatch.setattr(settings, "embedding_provider", "openai_compatible")
    monkeypatch.setattr(settings, "embedding_api_key", "sk-test")
    monkeypatch.setattr(settings, "embedding_base_url", vendor.base)
    monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")


@pytest.mark.asyncio
async def test_a_query_with_no_lexical_hit_still_returns_the_embedded_supplier(monkeypatch):
    vendor = Embeddings()
    async with embed_server(vendor):
        _enable(monkeypatch, vendor)
        async with SessionLocal() as session:
            tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
            steel = await create_supplier(
                session, tenant, name="Nile Steel Works", categories=["rebar"], status="approved",
            )
            cement = await create_supplier(
                session, tenant, name="Delta Cement", categories=["cement"], status="approved",
            )
            cement.overall_score = 99
            steel.overall_score = 10
            await session.flush()
            rows, total = await search_suppliers(session, tenant.id, "semantic-query-token")
            assert total >= 1
            assert rows[0].id == steel.id
            assert all(row.id != cement.id or rows[0].id == steel.id for row in rows[:1])
            assert isinstance(steel.embedding, list) and steel.embedding[0] == 1.0
            assert vendor.calls >= 2  # one per supplier at create, plus the query
            await session.rollback()


@pytest.mark.asyncio
async def test_embeddings_off_does_not_call_out_and_keeps_the_lexical_order(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "off")
    monkeypatch.setattr(settings, "embedding_base_url", "http://127.0.0.1:1/v1")
    async with SessionLocal() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
        await create_supplier(session, tenant, name="Lexical Only Steel", categories=["steel"])
        rows, _total = await search_suppliers(session, tenant.id, "Lexical Only")
        assert rows and rows[0].name == "Lexical Only Steel"
        stored = (await session.execute(
            select(Supplier).where(Supplier.name == "Lexical Only Steel")
        )).scalar_one()
        assert stored.embedding is None
        await session.rollback()
