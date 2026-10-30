"""Alembic environment (async engine, model metadata target)."""
from __future__ import annotations

import asyncio
import sys
import warnings
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models.entities import ALL_MODELS  # noqa: E402,F401  (register metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", settings.database_url)

# Alembic hard-codes `alembic_version.version_num` as varchar(32) and PostgreSQL enforces it
# (SQLite does not, which is how an over-long revision id can silently pass dev runs).
MAX_REVISION_ID_LENGTH = 32

# Objects that only exist as raw SQL because SQLAlchemy cannot express them: pgvector columns
# (`vector(1536)`) and the pg_trgm / HNSW indexes created by 0011. They are deliberately absent
# from the ORM metadata (SQLite would choke on both), so autogenerate must ignore them instead of
# proposing to drop them.
RAW_SQL_COLUMNS = frozenset({"embedding_vec"})
RAW_SQL_INDEXES = frozenset({
    "ix_supplier_name_trgm",
    "ix_supplier_name_ar_trgm",
    "ix_item_name_trgm",
    "ix_rfq_title_trgm",
    "ix_supplier_embedding_hnsw",
    "ix_item_embedding_hnsw",
})


def include_object(object_, name, type_, reflected, compare_to):  # noqa: ANN001, ANN201
    if type_ == "column" and name in RAW_SQL_COLUMNS:
        return False
    if type_ == "index" and name in RAW_SQL_INDEXES:
        return False
    return True


def _assert_revision_ids_fit() -> None:
    too_long = [
        revision.revision
        for revision in ScriptDirectory.from_config(config).walk_revisions()
        if len(revision.revision) > MAX_REVISION_ID_LENGTH
    ]
    if too_long:
        raise RuntimeError(
            "revision ids must be <= "
            f"{MAX_REVISION_ID_LENGTH} chars for PostgreSQL's alembic_version column: {too_long}"
        )


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:  # noqa: ANN001
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=connection.dialect.name == "sqlite",
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


# The pgvector type has no SQLAlchemy dialect here; reflection only needs its existence, which
# `include_object` already filters out of autogenerate.
warnings.filterwarnings("ignore", message="Did not recognize type 'vector'")

_assert_revision_ids_fit()

if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
