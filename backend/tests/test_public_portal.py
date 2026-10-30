"""No-login supplier surfaces: signed links, expiry, untrusted input handling (FR-PORTAL-*)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models.entities import RFQ, WebFormToken
from app.db.session import SessionLocal


async def _form_token(client, buyer) -> str:
    rfqs = await client.get("/api/v1/rfqs", headers=buyer["headers"])
    open_rfqs = [row for row in rfqs.json()["items"] if row["status"] not in {"closed", "cancelled"}]
    async with SessionLocal() as session:
        for rfq_row in open_rfqs:
            token = (await session.execute(
                select(WebFormToken).where(WebFormToken.rfq_id == rfq_row["id"]).limit(1)
            )).scalar_one_or_none()
            if token:
                return token.token
    return ""


async def test_public_form_requires_no_auth_and_shows_lines(client, buyer) -> None:
    token = await _form_token(client, buyer)
    assert token
    resp = await client.get(f"/api/v1/public/form/{token}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lines"] and body["rfq"]["code"]
    assert "unit_price" in body["fields"]
    assert "supplier" in body


async def test_public_form_submission_creates_offer_pending_review(client, buyer) -> None:
    token = await _form_token(client, buyer)
    resp = await client.post(f"/api/v1/public/form/{token}", json={
        "currency": "EGP",
        "lead_time_days": 7,
        "payment_terms": "Net 30",
        "lines": [{"description": "Rebar 12mm", "unit_price": "20,400", "quantity": "2500", "uom": "kg"}],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["extraction_id"] and body["offer_id"]
    assert body["needs_review"] is True
    # A public submission must never silently become a verified offer.
    extractions = await client.get("/api/v1/extractions", headers=buyer["headers"])
    rows = [row for row in extractions.json()["items"] if row["id"] == body["extraction_id"]]
    assert rows and rows[0]["status"] in {"parsed", "needs_review"}


async def test_public_form_rejects_invalid_payloads(client, buyer) -> None:
    token = await _form_token(client, buyer)
    empty = await client.post(f"/api/v1/public/form/{token}", json={"lines": []})
    assert empty.status_code == 422
    bad_price = await client.post(f"/api/v1/public/form/{token}", json={
        "lines": [{"description": "X", "unit_price": "0", "quantity": "1"}]})
    assert bad_price.status_code == 422
    bad_currency = await client.post(f"/api/v1/public/form/{token}", json={
        "currency": "XYZ", "lines": [{"description": "X", "unit_price": "10", "quantity": "1"}]})
    assert bad_currency.status_code == 422


async def test_unknown_and_expired_tokens_are_rejected(client) -> None:
    unknown = await client.get("/api/v1/public/form/not-a-real-token")
    assert unknown.status_code == 404
    async with SessionLocal() as session:
        token = (await session.execute(select(WebFormToken).limit(1))).scalar_one()
        expired = WebFormToken(tenant_id=token.tenant_id, rfq_id=token.rfq_id, supplier_id=token.supplier_id,
                               token="expired-token-for-tests",
                               expires_at=datetime.now(UTC) - timedelta(days=1))
        session.add(expired)
        await session.commit()
    resp = await client.get("/api/v1/public/form/expired-token-for-tests")
    assert resp.status_code == 422
    assert "expired" in resp.json()["detail"].lower()


async def test_closed_rfq_form_is_refused(client, buyer) -> None:
    async with SessionLocal() as session:
        rfq = (await session.execute(select(RFQ).where(RFQ.status == "closed"))).scalars().first()
        token = (await session.execute(select(WebFormToken).where(WebFormToken.rfq_id == rfq.id))).scalars().first()
    if not token:
        pytest.skip("no token bound to a closed RFQ in the demo data")
    resp = await client.get(f"/api/v1/public/form/{token.token}")
    assert resp.status_code in (409, 422)


async def test_supplier_portal_link_and_profile(client, manager) -> None:
    suppliers = await client.get("/api/v1/suppliers", headers=manager["headers"])
    supplier_id = suppliers.json()["items"][0]["id"]
    link = await client.post(f"/api/v1/suppliers/{supplier_id}/portal-link", headers=manager["headers"])
    assert link.status_code == 200, link.text
    token = link.json()["url"].rstrip("/").split("/")[-1]
    view = await client.get(f"/api/v1/public/supplier/{token}")
    assert view.status_code == 200
    assert view.json()["supplier"]["name"]
    # Contact details are masked for the public page.
    for contact in view.json()["contacts"]:
        assert "***" in contact["email"] or not contact["email"]
    submitted = await client.post(f"/api/v1/public/supplier/{token}", json={
        "categories": ["STEEL"], "website": "https://updated.example.com"})
    assert submitted.status_code == 200
    assert submitted.json()["pending_review"] is True
