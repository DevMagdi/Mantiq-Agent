"""Authentication, RBAC, tenant isolation and audit integrity (FR-AUTH-*, NFR-SEC-1, NFR-SEC-4)."""
from __future__ import annotations

import base64
import json

from app.core import rate_limit


async def test_health_and_ready(client) -> None:
    health = await client.get("/health")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    ready = await client.get("/ready")
    assert ready.status_code == 200


async def test_login_returns_tokens_and_sets_cookies(client) -> None:
    resp = await client.post("/api/v1/auth/login", json={"email": "buyer@nile.example.com",
                                                         "password": "Demo!Pilot2026"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] and body["token_type"] == "bearer"
    assert body["user"]["email"] == "buyer@nile.example.com"
    assert body["user"]["tenant"]["slug"] == "nile"
    assert "procureai_refresh" in resp.cookies
    assert "httponly" in resp.headers.get("set-cookie", "").lower()


async def test_login_with_wrong_password_is_rejected(client) -> None:
    resp = await client.post("/api/v1/auth/login", json={"email": "buyer@nile.example.com",
                                                         "password": "WrongPassword!1"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


async def test_protected_endpoint_requires_token(client) -> None:
    resp = await client.get("/api/v1/rfqs")
    assert resp.status_code == 401


async def test_tampered_token_is_rejected(client, buyer) -> None:
    header, payload, signature = buyer["token"].split(".")
    forged_payload = base64.urlsafe_b64encode(
        json.dumps({**json.loads(base64.urlsafe_b64decode(payload + "==")),
                    "sa": True, "roles": ["superadmin"]}).encode()
    ).decode().rstrip("=")
    forged = f"{header}.{forged_payload}.{signature}"
    resp = await client.get("/api/v1/rfqs", headers={"authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


async def test_me_returns_permissions(client, buyer) -> None:
    resp = await client.get("/api/v1/auth/me", headers=buyer["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert "rfq:create" in body["permissions"]
    assert body["email"] == "buyer@nile.example.com"
    assert body["tenant"]["slug"] == "nile"


async def test_requester_cannot_create_supplier(client, requester) -> None:
    resp = await client.post("/api/v1/suppliers", headers=requester["headers"],
                             json={"name": "Should Fail Trading", "country": "EG"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


async def test_manager_can_create_supplier(client, manager) -> None:
    resp = await client.post("/api/v1/suppliers", headers=manager["headers"],
                             json={"name": "New Steel Trading LLC", "name_ar": "شركة الصلب الجديدة",
                                   "country": "EG", "city": "Cairo", "categories": ["STEEL"]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "New Steel Trading LLC"


async def test_cross_tenant_access_returns_404(client, manager, second_tenant) -> None:
    """Tenant isolation: a foreign id must look like it does not exist (no existence oracle)."""
    rfqs = await client.get("/api/v1/rfqs", headers=manager["headers"])
    foreign_id = rfqs.json()["items"][0]["id"]
    login = await client.post("/api/v1/auth/login",
                              json={"email": "owner@isolation.example.com", "password": "Demo!Pilot2026"})
    assert login.status_code == 200, login.text
    other = {"authorization": f"Bearer {login.json()['access_token']}"}
    assert login.json()["user"]["tenant"]["slug"] == "isolation-co"
    resp = await client.get(f"/api/v1/rfqs/{foreign_id}", headers=other)
    assert resp.status_code == 404, resp.text
    # And their own lists must be empty rather than leaking Nile rows.
    listing = await client.get("/api/v1/rfqs", headers=other)
    assert listing.status_code == 200 and listing.json()["items"] == []


async def test_internal_fields_are_not_exposed(client, manager) -> None:
    rfqs = await client.get("/api/v1/rfqs", headers=manager["headers"])
    row = rfqs.json()["items"][0]
    assert "search_vector" not in row and "embedding" not in row


async def test_audit_trail_records_writes(client, manager) -> None:
    created = await client.post("/api/v1/suppliers", headers=manager["headers"],
                                json={"name": "Audit Trail Co", "country": "AE"})
    assert created.status_code == 201
    audit = await client.get("/api/v1/audit", headers=manager["headers"], params={"action": "supplier.created"})
    assert audit.status_code == 200
    assert any(item["action"] == "supplier.created" for item in audit.json()["items"])


async def test_rate_limiter_blocks_repeated_logins(client, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    rate_limit.reset()
    statuses = []
    for _ in range(25):
        resp = await client.post("/api/v1/auth/login", json={"email": "buyer@nile.example.com",
                                                             "password": "WrongPassword!1"})
        statuses.append(resp.status_code)
    assert 429 in statuses
    rate_limit.reset()


async def test_logout_revokes_session(client, buyer) -> None:
    resp = await client.post("/api/v1/auth/logout", headers=buyer["headers"])
    assert resp.status_code in (200, 204)
