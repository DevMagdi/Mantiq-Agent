"""Supplier embeddings and similarity ranking.

Lexical search (``ILIKE`` / trigram) stays the default. This module only runs when
``EMBEDDING_PROVIDER=openai_compatible`` and a key is set. It calls a real
``/v1/embeddings`` endpoint (OpenAI, or any compatible gateway). When the provider
is off, or the call fails, callers keep the lexical order — nothing here invents a
vector or pretends a keyword hit is semantic.

On PostgreSQL the same vector is also written to ``embedding_vec`` (migration 0011,
``vector(1536)``) so the HNSW index can serve nearest-neighbour queries. SQLite
(the test suite) stores the JSON ``embedding`` column only; cosine is computed in
process over that column. A dimension mismatch is stored as JSON and skipped for
the vector column rather than crashing the write.
"""
from __future__ import annotations

import logging
import math
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.entities import Supplier

log = logging.getLogger("procureai.embeddings")

_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


def provider_configured() -> bool:
    """True only when an operator turned embeddings on *and* gave a credential."""
    if (settings.embedding_provider or "").lower() != "openai_compatible":
        return False
    return bool(settings.embedding_api_key or settings.llm_api_key)


def _endpoint() -> str:
    base = (settings.embedding_base_url or settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
    return f"{base}/embeddings"


def _api_key() -> str:
    return settings.embedding_api_key or settings.llm_api_key


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    norm_l = 0.0
    norm_r = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        norm_l += a * a
        norm_r += b * b
    if norm_l == 0.0 or norm_r == 0.0:
        return 0.0
    return dot / math.sqrt(norm_l * norm_r)


def supplier_text(supplier: Supplier) -> str:
    categories = " ".join(str(item) for item in (supplier.categories or []))
    return " ".join(part for part in (supplier.name, supplier.name_ar, supplier.city, categories) if part).strip()


async def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """One embeddings call. ``None`` means "not configured" — a failure raises.

    Callers that must not fail a user action catch the exception and fall back
    to lexical ranking. Returning an empty list for a configured-but-broken
    provider would look like a successful empty result, so this raises instead.
    """
    cleaned = [text.strip() for text in texts if text and text.strip()]
    if not cleaned or not provider_configured():
        return None
    payload = {"model": settings.embedding_model, "input": cleaned}
    headers = {"authorization": f"Bearer {_api_key()}", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(_endpoint(), json=payload, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(f"embeddings HTTP {response.status_code}: {response.text[:180]}")
    body = response.json()
    rows = list(body.get("data") or [])
    rows.sort(key=lambda row: int(row.get("index") or 0))
    vectors: list[list[float]] = []
    for row in rows:
        raw = row.get("embedding")
        if not isinstance(raw, list) or not raw:
            raise RuntimeError("embeddings response is missing a vector")
        vectors.append([float(value) for value in raw])
    if len(vectors) != len(cleaned):
        raise RuntimeError(f"embeddings returned {len(vectors)} vectors for {len(cleaned)} inputs")
    return vectors


async def _write_pgvector(session: AsyncSession, supplier_id: str, vector: list[float]) -> None:
    """Persist the vector column on PostgreSQL only. Other dialects keep the JSON copy."""
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    expected = int(settings.embedding_dimensions)
    if len(vector) != expected:
        log.warning(
            "embedding length %s != EMBEDDING_DIMENSIONS %s; JSON stored, vector column skipped",
            len(vector), expected,
        )
        return
    literal = "[" + ",".join(f"{value:.8f}" for value in vector) + "]"
    await session.execute(
        text("UPDATE supplier SET embedding_vec = CAST(:vector AS vector) WHERE id = :id"),
        {"vector": literal, "id": supplier_id},
    )


async def index_supplier(session: AsyncSession, supplier: Supplier) -> bool:
    """Store an embedding for one supplier. Returns False when nothing was written.

    A provider outage must not fail supplier creation: the lexical index still
    finds the row, and a later reindex can fill the vector.
    """
    if not provider_configured():
        return False
    text_value = supplier_text(supplier)
    if not text_value:
        return False
    try:
        vectors = await embed_texts([text_value])
    except Exception as exc:  # provider outage is non-fatal for the write path
        log.warning("embedding failed supplier=%s: %s", supplier.id, exc)
        return False
    if not vectors:
        return False
    supplier.embedding = vectors[0]
    await _write_pgvector(session, supplier.id, vectors[0])
    await session.flush()
    return True


def _as_vector(value: Any) -> list[float] | None:
    if isinstance(value, list) and value and all(isinstance(item, (int, float)) for item in value):
        return [float(item) for item in value]
    return None


async def rerank(query: str, rows: list[Supplier]) -> list[Supplier]:
    """Reorder a lexical page by cosine similarity. Untouched when embeddings are off."""
    if not rows or not query.strip() or not provider_configured():
        return rows
    try:
        vectors = await embed_texts([query])
    except Exception as exc:
        log.warning("embedding rerank skipped: %s", exc)
        return rows
    if not vectors:
        return rows
    query_vec = vectors[0]

    def sort_key(supplier: Supplier) -> tuple[float, float]:
        stored = _as_vector(supplier.embedding)
        similarity = cosine(query_vec, stored) if stored else -1.0
        return (similarity, float(supplier.overall_score or 0))

    return sorted(rows, key=sort_key, reverse=True)


async def nearest(session: AsyncSession, tenant_id: str, query: str, *, limit: int = 20,
                  floor: float = 0.2) -> list[Supplier]:
    """Suppliers closest to ``query`` when the lexical filter matched nothing.

    PostgreSQL uses ``embedding_vec <=>`` (the HNSW index). Everywhere else the
    JSON column is scored in process — the same vectors, no second algorithm.
    """
    if not query.strip() or not provider_configured():
        return []
    try:
        vectors = await embed_texts([query])
    except Exception as exc:
        log.warning("embedding nearest skipped: %s", exc)
        return []
    if not vectors:
        return []
    query_vec = vectors[0]
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        literal = "[" + ",".join(f"{value:.8f}" for value in query_vec) + "]"
        found = await session.execute(
            text(
                "SELECT id FROM supplier "
                "WHERE tenant_id = :tenant AND deleted_at IS NULL AND embedding_vec IS NOT NULL "
                "ORDER BY embedding_vec <=> CAST(:vector AS vector) "
                "LIMIT :limit"
            ),
            {"tenant": tenant_id, "vector": literal, "limit": limit},
        )
        ids = [row[0] for row in found]
        if not ids:
            return []
        rows = list((await session.execute(
            select(Supplier).where(Supplier.id.in_(ids))
        )).scalars().all())
        order = {supplier_id: index for index, supplier_id in enumerate(ids)}
        rows.sort(key=lambda supplier: order.get(supplier.id, len(ids)))
        return rows

    rows = list((await session.execute(
        select(Supplier).where(
            Supplier.tenant_id == tenant_id,
            Supplier.deleted_at.is_(None),
            Supplier.embedding.is_not(None),
        )
    )).scalars().all())
    scored: list[tuple[float, Supplier]] = []
    for supplier in rows:
        stored = _as_vector(supplier.embedding)
        if not stored:
            continue
        similarity = cosine(query_vec, stored)
        if similarity >= floor:
            scored.append((similarity, supplier))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [supplier for _similarity, supplier in scored[:limit]]
