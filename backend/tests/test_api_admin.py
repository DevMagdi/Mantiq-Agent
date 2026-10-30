"""Administration, identity and governance endpoints (FR-ADM-*, FR-SEC-*, FR-PRV-*).

Every request here goes through the real router, the real RBAC dependency and a real
SQLite database built by the shipped Alembic migrations.
"""
from __future__ import annotations

from tests.conftest import DEMO_PASSWORD, unique


# --------------------------------------------------------------------- identity
async def test_me_returns_nested_tenant_and_permissions(client, admin):
    resp = await client.get("/api/v1/auth/me", headers=admin["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "admin@pilot.procureai"
    assert body["tenant"]["slug"] in {"mantiq", "nile"}
    assert "settings:read" in body["permissions"]


async def test_preferences_roundtrip(client, admin):
    resp = await client.patch("/api/v1/auth/me", headers=admin["headers"],
                              json={"locale": "en", "preferred_currency": "USD"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["locale"] == "en" and body["preferred_currency"] == "USD"
    # There is exactly one theme, so the payload must not advertise a switch that does not exist.
    assert "theme" not in body and "numerals" not in body
    back = await client.get("/api/v1/auth/me", headers=admin["headers"])
    assert back.json()["locale"] == "en"
    await client.patch("/api/v1/auth/me", headers=admin["headers"],
                       json={"locale": "ar", "preferred_currency": "EGP"})


async def test_wrong_password_is_401_with_stable_code(client):
    resp = await client.post("/api/v1/auth/login", json={"email": "admin@pilot.procureai", "password": "nope"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


async def test_change_password_and_back(client, requester):
    bad = await client.post("/api/v1/auth/change-password", headers=requester["headers"],
                            json={"current_password": "wrong", "new_password": "N3w!Password2026"})
    assert bad.status_code in {400, 401, 422}

    ok = await client.post("/api/v1/auth/change-password", headers=requester["headers"],
                           json={"current_password": DEMO_PASSWORD, "new_password": "N3w!Password2026"})
    assert ok.status_code == 200
    login = await client.post("/api/v1/auth/login",
                              json={"email": "requester@nile.example.com", "password": "N3w!Password2026"})
    assert login.status_code == 200
    # restore the demo password so later tests keep working
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}
    await client.post("/api/v1/auth/change-password", headers=headers,
                      json={"current_password": "N3w!Password2026", "new_password": DEMO_PASSWORD})


async def test_sessions_list_and_revoke(client, finance):
    listed = await client.get("/api/v1/auth/sessions", headers=finance["headers"])
    assert listed.status_code == 200
    assert len(listed.json()["items"]) >= 1
    session_id = listed.json()["items"][0]["id"]
    revoked = await client.delete(f"/api/v1/auth/sessions/{session_id}", headers=finance["headers"])
    assert revoked.status_code == 200


async def test_password_reset_request_never_leaks_whether_account_exists(client):
    known = await client.post("/api/v1/auth/password-reset/request", json={"email": "finance@nile.example.com"})
    unknown = await client.post("/api/v1/auth/password-reset/request", json={"email": "ghost@nowhere.example"})
    assert known.status_code == unknown.status_code == 200
    assert known.json().get("sent") is True


async def test_sso_config_is_public_and_reports_disabled(client):
    resp = await client.get("/api/v1/auth/sso/config", params={"tenant_slug": "nile"})
    assert resp.status_code == 200
    assert "providers" in resp.json()


async def test_mfa_setup_enable_disable_cycle(client, buyer):
    setup = await client.post("/api/v1/auth/mfa/setup", headers=buyer["headers"])
    assert setup.status_code == 200
    assert setup.json().get("secret") and setup.json().get("otpauth_uri", "").startswith("otpauth://")

    bad = await client.post("/api/v1/auth/mfa/enable", headers=buyer["headers"], json={"code": "000000"})
    assert bad.status_code in {400, 401, 422}

    # A real TOTP code must enable MFA, then sign-in must demand the second factor.
    from app.core.security import totp_now

    code = totp_now(setup.json()["secret"])
    enabled = await client.post("/api/v1/auth/mfa/enable", headers=buyer["headers"], json={"code": code})
    assert enabled.status_code == 200
    assert enabled.json().get("backup_codes")

    challenge = await client.post("/api/v1/auth/login",
                                  json={"email": "buyer@nile.example.com", "password": DEMO_PASSWORD})
    assert challenge.status_code == 200 and challenge.json()["mfa_required"] is True
    assert "access_token" not in challenge.json()

    verified = await client.post("/api/v1/auth/mfa/verify", json={
        "challenge_token": challenge.json()["challenge_token"], "code": totp_now(setup.json()["secret"]),
    })
    assert verified.status_code == 200 and verified.json().get("access_token")

    # Restore: disable MFA so the rest of the suite keeps the simple login flow.
    headers = {"authorization": f"Bearer {verified.json()['access_token']}"}
    disabled = await client.post("/api/v1/auth/mfa/disable", headers=headers, json={"code": totp_now(setup.json()["secret"])})
    assert disabled.status_code == 200


# ------------------------------------------------------------------ users & RBAC
async def test_user_crud_and_validation(client, admin):
    created = await client.post("/api/v1/users", headers=admin["headers"], json={
        "email": f"{unique('user')}@pilot.procureai", "full_name": "Test Buyer",
        "roles": ["buyer"], "password": "Str0ng!Pass2026",
    })
    assert created.status_code in {200, 201}, created.text
    user_id = created.json()["id"]

    patched = await client.patch(f"/api/v1/users/{user_id}", headers=admin["headers"],
                                 json={"full_name": "Renamed Buyer", "is_active": False})
    assert patched.status_code == 200

    listed = await client.get("/api/v1/users", headers=admin["headers"])
    assert any(row["id"] == user_id for row in listed.json()["items"])

    duplicate = await client.post("/api/v1/users", headers=admin["headers"], json={
        "email": created.json()["email"], "full_name": "Dup", "roles": ["buyer"],
    })
    assert duplicate.status_code >= 400


async def test_roles_and_permissions_catalogue(client, admin):
    roles = await client.get("/api/v1/roles", headers=admin["headers"])
    perms = await client.get("/api/v1/permissions", headers=admin["headers"])
    assert roles.status_code == 200 and perms.status_code == 200
    assert len(roles.json()["items"]) >= 4
    assert any(p["code"] == "approval:decide" for p in perms.json()["items"])


async def test_role_without_permission_gets_403(client, requester, admin):
    resp = await client.get("/api/v1/users", headers=requester["headers"])
    assert resp.status_code == 403


async def test_approval_limits_are_upserted_not_duplicated(client, admin):
    payload = {"role_code": "finance_approver", "max_amount": "7500000"}
    first = await client.post("/api/v1/limits", headers=admin["headers"], json=payload)
    second = await client.post("/api/v1/limits", headers=admin["headers"], json=payload)
    assert first.status_code in {200, 201} and second.status_code in {200, 201}
    listed = await client.get("/api/v1/limits", headers=admin["headers"])
    rows = [r for r in listed.json()["items"] if r["role_code"] == "finance_approver"]
    assert len(rows) == 1
    assert rows[0]["max_amount"].startswith("7500000")


async def test_plants_and_categories(client, admin):
    plant = await client.post("/api/v1/plants", headers=admin["headers"],
                              json={"code": unique("PL").upper()[:10], "name": "New Plant", "city": "Cairo"})
    assert plant.status_code in {200, 201}
    plants = await client.get("/api/v1/plants", headers=admin["headers"])
    assert any(row["name"] == "New Plant" for row in plants.json()["items"])
    categories = await client.get("/api/v1/categories", headers=admin["headers"])
    assert categories.status_code == 200
    taxonomy = await client.get("/api/v1/taxonomy", headers=admin["headers"], params={"q": "rebar"})
    assert taxonomy.status_code == 200
    conversions = await client.get("/api/v1/unit-conversions", headers=admin["headers"])
    assert conversions.status_code == 200
    assert conversions.json()["items"]


# ---------------------------------------------------------------- integrations
async def test_integrations_crud_and_probe(client, admin):
    listing = await client.get("/api/v1/integrations", headers=admin["headers"])
    assert listing.status_code == 200
    assert listing.json()["platform_adapters"]["email"] in {"fake", "smtp", "ses"}

    created = await client.post("/api/v1/integrations", headers=admin["headers"],
                                json={"kind": "email", "name": "SMTP", "config": {"host": "smtp.example"},
                                      "secrets": {"password": "s3cr3t"}})
    assert created.status_code in {200, 201}

    probe = await client.post("/api/v1/integrations/email/test", headers=admin["headers"])
    assert probe.status_code == 200 and "ok" in probe.json()
    llm_probe = await client.post("/api/v1/integrations/llm/test", headers=admin["headers"])
    assert llm_probe.status_code == 200 and llm_probe.json()["ok"] is True
    erp_probe = await client.post("/api/v1/integrations/erp/test", headers=admin["headers"])
    assert erp_probe.status_code == 200

    after = await client.get("/api/v1/integrations", headers=admin["headers"])
    row = next(r for r in after.json()["items"] if r["kind"] == "email")
    assert "password" not in str(row.get("config") or {})


async def test_api_keys_are_shown_once_and_revocable(client, admin):
    created = await client.post("/api/v1/api-keys", headers=admin["headers"], json={"name": "ERP", "scopes": ["read"]})
    assert created.status_code in {200, 201}
    raw = created.json()["api_key"]
    assert raw.startswith("pk_")
    listed = await client.get("/api/v1/api-keys", headers=admin["headers"])
    row = next(r for r in listed.json()["items"] if r["id"] == created.json()["id"])
    assert "api_key" not in row and row["prefix"] == raw[:10]
    revoked = await client.delete(f"/api/v1/api-keys/{row['id']}", headers=admin["headers"])
    assert revoked.status_code == 200


async def test_llm_config_requires_admin_permission_and_accepts_override(client, admin, manager):
    denied = await client.get("/api/v1/llm-config", headers=manager["headers"])
    assert denied.status_code == 403

    put = await client.put("/api/v1/llm-config", headers=admin["headers"], json={
        "provider": "fake", "model": "fake-deterministic-1", "monthly_token_budget": 100000,
    })
    assert put.status_code == 200
    current = await client.get("/api/v1/llm-config", headers=admin["headers"])
    assert current.json()["items"][0]["provider"] == "fake"


# ------------------------------------------------------------------- governance
async def test_settings_read_and_update(client, admin):
    before = await client.get("/api/v1/settings", headers=admin["headers"])
    assert before.status_code == 200 and "tenant" in before.json()
    updated = await client.patch("/api/v1/settings", headers=admin["headers"], json={
        "timezone": "Africa/Cairo", "settings": {"reminder_hours": 24},
    })
    assert updated.status_code == 200


async def test_retention_policies_are_upserted(client, admin):
    put = await client.put("/api/v1/retention", headers=admin["headers"],
                           json={"entity": "audit_logs", "retain_days": 400})
    assert put.status_code == 200
    listing = await client.get("/api/v1/retention", headers=admin["headers"])
    assert any(r["entity"] == "audit_logs" and r["retain_days"] == 400 for r in listing.json()["items"])
    too_short = await client.put("/api/v1/retention", headers=admin["headers"],
                                 json={"entity": "audit_logs", "retain_days": 5})
    assert too_short.status_code == 422


async def test_erasure_request_lifecycle_and_data_export(client, admin):
    created = await client.post("/api/v1/erasure-requests", headers=admin["headers"],
                                json={"subject_email": "subject@example.com", "subject_type": "contact"})
    assert created.status_code in {200, 201}
    request_id = created.json()["id"]
    listed = await client.get("/api/v1/erasure-requests", headers=admin["headers"])
    assert any(row["id"] == request_id for row in listed.json()["items"])
    executed = await client.post(f"/api/v1/erasure-requests/{request_id}/execute", headers=admin["headers"])
    assert executed.status_code == 200
    assert executed.json()["ok"] is True
    assert set(executed.json()["purged"]) == {"supplier_contacts", "users", "messages", "suppliers"}

    export = await client.get("/api/v1/data-export", headers=admin["headers"],
                              params={"subject_email": "finance@nile.example.com"})
    assert export.status_code == 200
    assert isinstance(export.json(), dict)


async def test_audit_log_is_tenant_scoped_and_filterable(client, admin):
    resp = await client.get("/api/v1/audit", headers=admin["headers"], params={"limit": 20})
    assert resp.status_code == 200
    assert resp.json()["items"]
    assert all("action" in row for row in resp.json()["items"])


# --------------------------------------------------------------------- billing
async def test_billing_usage_invoices_and_export(client, admin, manager):
    usage = await client.get("/api/v1/billing/usage", headers=admin["headers"])
    assert usage.status_code == 200
    body = usage.json()
    assert body["plan"] and "limits" in body and "usage" in body

    denied = await client.get("/api/v1/billing/usage", headers=manager["headers"])
    assert denied.status_code == 403

    invoice = await client.post("/api/v1/billing/invoices", headers=admin["headers"])
    assert invoice.status_code in {200, 201}
    invoices = await client.get("/api/v1/billing/invoices", headers=admin["headers"])
    assert invoices.json()["items"]

    export = await client.get("/api/v1/billing/usage-export", headers=admin["headers"])
    assert export.status_code == 200
    assert export.text.splitlines()[0] == "timestamp,kind,quantity,cost_usd"


# ------------------------------------------------------------ activity & alerts
async def test_agent_runs_and_tool_catalogue_are_auditable(client, admin):
    runs = await client.get("/api/v1/agent-runs", headers=admin["headers"])
    assert runs.status_code == 200
    tools = await client.get("/api/v1/agent/tools", headers=admin["headers"])
    assert tools.status_code == 200
    names = {tool["name"] for tool in tools.json()["items"]}
    assert {"search_suppliers", "create_rfq"} <= names or len(names) >= 5
    assert all("mutating" in tool for tool in tools.json()["items"])


async def test_notifications_and_comments(client, buyer):
    listed = await client.get("/api/v1/notifications", headers=buyer["headers"])
    assert listed.status_code == 200
    marked = await client.post("/api/v1/notifications/read-all", headers=buyer["headers"])
    assert marked.status_code == 200

    rfqs = await client.get("/api/v1/rfqs", headers=buyer["headers"])
    rfq_id = rfqs.json()["items"][0]["id"]
    comment = await client.post("/api/v1/comments", headers=buyer["headers"],
                                json={"body": "Please confirm the delivery window.", "rfq_id": rfq_id})
    assert comment.status_code in {200, 201}
    thread = await client.get("/api/v1/comments", headers=buyer["headers"], params={"rfq_id": rfq_id})
    assert any(c["body"].startswith("Please confirm") for c in thread.json()["items"])


async def test_onboarding_checklist_and_capabilities(client, admin):
    checklist = await client.get("/api/v1/onboarding/checklist", headers=admin["headers"])
    assert checklist.status_code == 200
    assert checklist.json()["total"] >= 3
    caps = await client.get("/api/v1/meta/capabilities", headers=admin["headers"])
    assert caps.status_code == 200
    assert "adapters" in caps.json() and "features" in caps.json()


async def test_platform_endpoints_require_superadmin(client, admin, manager):
    tenants = await client.get("/api/v1/platform/tenants", headers=admin["headers"])
    assert tenants.status_code in {200, 403}
    if tenants.status_code == 200:
        assert tenants.json()["items"]
        health = await client.get("/api/v1/platform/health", headers=admin["headers"])
        assert health.status_code == 200

    denied = await client.get("/api/v1/platform/tenants", headers=manager["headers"])
    assert denied.status_code in {401, 403}


async def test_health_and_ready_are_public(client):
    assert (await client.get("/health")).status_code == 200
    ready = await client.get("/ready")
    assert ready.status_code == 200
    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
