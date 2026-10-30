"""PII must fail closed instead of taking a screen down.

Rotating `SECRET_KEY`/`ENCRYPTION_MASTER_KEY` without re-wrapping the tenant data keys is an
operational mistake that must never surface as a 500: the ciphertext stays in the database, the
read returns nothing, and the list still renders. This was found by the 50-user load test, which
saw random 500s on `GET /suppliers` after the demo data had been seeded with a different secret.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core import crypto
from app.core.config import settings
from app.db.models.entities import Supplier, Tenant
from app.db.session import SessionLocal
from app.services.suppliers import contact_pii, primary_contact, upsert_contact


async def _tenant_and_supplier(session):
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == "nile"))).scalar_one()
    supplier = (await session.execute(
        select(Supplier).where(Supplier.tenant_id == tenant.id)
    )).scalars().first()
    return tenant, supplier


async def test_a_master_key_mismatch_withholds_pii_instead_of_raising():
    async with SessionLocal() as session:
        tenant, supplier = await _tenant_and_supplier(session)
        contact = await primary_contact(session, tenant, supplier.id)
        if contact is None:
            contact = await upsert_contact(session, tenant, supplier, name="Crypto Contact",
                                           email="crypto@example.com", phone="+201000009999")
        await session.commit()
        email_before, _phone = await contact_pii(session, tenant, contact)
        assert email_before, "the fixture needs a readable contact first"

        original = settings.encryption_master_key
        crypto._DEK_CACHE.clear()
        settings.encryption_master_key = "a-completely-different-master-key"
        try:
            with pytest.raises(crypto.CryptoError):
                await crypto.get_dek(session, tenant)
            # Reads degrade to empty (fail closed) rather than exploding…
            assert await contact_pii(session, tenant, contact) == ("", "")
            # …and the PII is still in the database, encrypted.
            assert contact.email and contact.email != email_before
        finally:
            settings.encryption_master_key = original
            crypto._DEK_CACHE.clear()

        # With the correct key restored, everything is readable again — nothing was re-keyed.
        assert (await contact_pii(session, tenant, contact))[0] == email_before


async def test_supplier_list_still_renders_when_pii_cannot_be_decrypted(client, manager):
    from app.core import crypto as crypto_module

    async with SessionLocal() as session:
        tenant, supplier = await _tenant_and_supplier(session)
        if await primary_contact(session, tenant, supplier.id) is None:
            await upsert_contact(session, tenant, supplier, name="Crypto Contact 2",
                                 email="crypto2@example.com", phone="+201000009998")
        await session.commit()

    original = settings.encryption_master_key
    crypto_module._DEK_CACHE.clear()
    settings.encryption_master_key = "another-wrong-master-key"
    try:
        response = await client.get("/api/v1/suppliers?page_size=5", headers=manager["headers"])
    finally:
        settings.encryption_master_key = original
        crypto_module._DEK_CACHE.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"], "the supplier list must still render"
    assert all(item.get("email", "") in ("", None) for item in body["items"])
