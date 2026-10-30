"""The UI permission map must be a subset of the backend RBAC matrix.

`frontend/src/lib/permissions.ts` mirrors `app/core/rbac.py`. A typo there would silently hide a
screen for every role (or, worse, show one that then 403s), which is exactly the class of bug this
test prevents. It also checks the role → screen expectations used for the reviewer walkthrough.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.core import rbac

PERMISSIONS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "permissions.ts"


def _frontend_permissions() -> set[str]:
    source = PERMISSIONS_TS.read_text(encoding="utf-8")
    return set(re.findall(r'"([a-z_]+:[a-z_]+)"', source))


def test_every_ui_permission_exists_in_the_backend_matrix():
    used = _frontend_permissions()
    assert used, "no permission strings found — did permissions.ts move?"
    unknown = sorted(used - set(rbac.ALL_PERMISSIONS))
    assert not unknown, f"UI references permissions the backend does not define: {unknown}"


def test_requester_cannot_reach_governance_screens():
    allowed = rbac.permissions_for(["requester"])
    # The UI hides /approvals and /activity for this role, so no request may be fired at them.
    for permission in ("approval:read", "audit:read"):
        assert permission not in allowed, f"requester must not hold {permission}"
    for permission in ("pr:read", "rfq:read", "po:read", "report:read"):
        assert permission in allowed, f"requester needs {permission} to use the app at all"


def test_reviewer_personas_hold_the_post_award_permissions():
    finance = rbac.permissions_for(["finance_approver"])
    assert {"po:read", "invoice:approve", "payment:record", "ledger:read"} <= finance
    # Stores record what arrived; the requester (not the warehouse) confirms the delivery.
    warehouse = rbac.permissions_for(["warehouse"])
    assert {"po:read", "receipt:create"} <= warehouse
    assert "receipt:confirm" not in warehouse
    assert "receipt:confirm" in rbac.permissions_for(["requester"])
    manager = rbac.permissions_for(["procurement_manager"])
    assert {"approval:read", "approval:decide", "report:read"} <= manager
