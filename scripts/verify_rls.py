#!/usr/bin/env python
"""Prove that PostgreSQL row level security actually isolates tenants.

Run against a migrated PostgreSQL database (see `alembic upgrade head`):

    DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/procureai python scripts/verify_rls.py

The script drops into the least-privilege application role (`procureai_app`, created by migration
0010), points the `app.tenant_id` GUC at each tenant in turn and checks four things:

1. a tenant sees its own rows,
2. a tenant sees zero rows belonging to another tenant,
3. a tenant cannot move a row into another tenant (WITH CHECK),
4. `audit_log` rejects UPDATE and DELETE (append-only trigger).

Exit code 0 means every guarantee holds; anything else prints a FAIL line and exits 1.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import asyncpg

DSN = os.environ.get("RLS_DSN") or os.environ.get("DATABASE_URL", "")
if DSN.startswith("postgresql+asyncpg://"):
    DSN = DSN.replace("postgresql+asyncpg://", "postgresql://", 1)
APP_ROLE = os.environ.get("RLS_APP_ROLE", "procureai_app")

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


async def main() -> int:
    if not DSN:
        print("DATABASE_URL (or RLS_DSN) must point at a migrated PostgreSQL database")
        return 2
    conn = await asyncpg.connect(DSN)
    try:
        # Prefer a tenant that actually owns data, so "sees its own rows" is a real assertion.
        tenants = [
            row["id"]
            for row in await conn.fetch(
                "SELECT t.id, count(s.id) AS suppliers FROM tenant t "
                "LEFT JOIN supplier s ON s.tenant_id = t.id GROUP BY t.id ORDER BY 2 DESC, 1 LIMIT 2"
            )
        ]
        if len(tenants) < 2:
            # A fresh install has one tenant; create a throwaway second one to test isolation.
            extra = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO tenant (id, name, slug, kind, locale, status, created_at, updated_at) "
                "VALUES ($1, 'RLS Probe', $2, 'customer', 'ar', 'active', now(), now())",
                extra, f"rls-probe-{extra[:8]}",
            )
            tenants.append(extra)
            print(f"note  created a throwaway second tenant for the check")

        await conn.execute(f"SET ROLE {APP_ROLE}")
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenants[0])

        own = await conn.fetchval("SELECT count(*) FROM supplier")
        check("tenant sees its own suppliers", own > 0, f"{own} rows")

        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenants[1])
        foreign = await conn.fetchval("SELECT count(*) FROM supplier")
        check("another tenant's suppliers are invisible", foreign == 0, f"{foreign} rows")

        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenants[0])
        supplier = await conn.fetchval("SELECT id FROM supplier LIMIT 1")
        if supplier is None:
            print("skip  cross-tenant write checks need at least one supplier row")
        else:
            try:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE supplier SET tenant_id = $1 WHERE id = $2", tenants[1], supplier
                    )
            except asyncpg.InsufficientPrivilegeError:
                check("cross-tenant move is blocked by WITH CHECK", True)
            else:
                check("cross-tenant move is blocked by WITH CHECK", False, "the UPDATE succeeded")

            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenants[1])
            touched = await conn.execute("UPDATE supplier SET city = city WHERE id = $1", supplier)
            check("another tenant's row cannot be updated", touched.endswith(" 0"), touched)

        # Two independent guards: the append-only trigger, and the missing UPDATE/DELETE grant
        # for the application role. Either one refusing the statement is a pass.
        for statement in ("UPDATE audit_log SET action = action", "DELETE FROM audit_log"):
            label = f"audit_log rejects {statement.split()[0]}"
            try:
                await conn.execute(statement)
            except asyncpg.RaiseError as exc:
                check(label, "append-only" in str(exc), "trigger")
            except asyncpg.InsufficientPrivilegeError:
                check(label, True, "no grant for the app role")
            else:
                check(label, False, "statement ran")
    finally:
        await conn.close()

    if failures:
        print(f"\n{len(failures)} guarantee(s) not met: {', '.join(failures)}")
        return 1
    print("\nAll row level security guarantees hold.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
