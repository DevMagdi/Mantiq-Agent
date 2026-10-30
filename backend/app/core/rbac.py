"""Single source of truth for the RBAC permission matrix (FR-AUTH-1..3).

Enforced in one dependency layer (`require`) and mirrored in the UI. The matrix is
materialised into `role` / `role_permission` by migration 0012 and re-syncable at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass

# Role codes are stable API values (never translated).
ROLES: dict[str, dict[str, str]] = {
    "superadmin": {"name": "Platform SuperAdmin", "name_ar": "مدير المنصة"},
    "admin": {"name": "Administrator", "name_ar": "مدير النظام"},
    "procurement_manager": {"name": "Procurement Manager", "name_ar": "مدير المشتريات"},
    "buyer": {"name": "Buyer", "name_ar": "مسؤول شراء"},
    "requester": {"name": "Plant / Department Requester", "name_ar": "طالب من المصنع أو القسم"},
    "finance_approver": {"name": "Finance Approver", "name_ar": "معتمد مالي"},
}

ALL_PERMISSIONS: dict[str, str] = {
    "pr:read": "View purchase requests",
    "pr:create": "Create purchase requests",
    "pr:update": "Edit purchase requests",
    "pr:delete": "Delete purchase requests",
    "pr:verify": "Confirm extracted / normalized request data",
    "rfq:read": "View RFQs",
    "rfq:create": "Create RFQs",
    "rfq:update": "Edit RFQs",
    "rfq:send": "Send RFQs to suppliers",
    "rfq:cancel": "Cancel RFQs",
    "supplier:read": "View suppliers",
    "supplier:create": "Create suppliers",
    "supplier:update": "Edit suppliers",
    "supplier:import": "Import supplier lists",
    "supplier:discovery": "Run autonomous supplier discovery",
    "supplier:approve": "Approve discovered suppliers / override blocks",
    "offer:read": "View offers",
    "offer:verify": "Verify extracted offer fields",
    "negotiation:read": "View negotiation threads",
    "negotiation:policy": "Configure negotiation policy",
    "negotiation:draft": "Draft negotiation messages",
    "negotiation:send": "Send negotiation messages",
    "negotiation:auto": "Enable semi-auto / auto negotiation",
    "comparison:read": "View comparison and recommendations",
    "approval:read": "View approval inbox",
    "approval:decide": "Approve / reject / split award",
    "approval:delegate": "Delegate approvals",
    "po:create": "Create purchase orders",
    "po:export": "Export / push purchase orders",
    "report:read": "View analytics and reports",
    "comment:create": "Comment and mention colleagues",
    "audit:read": "View audit trail and agent activity",
    "admin:users": "Manage users",
    "admin:roles": "Manage roles and permissions",
    "admin:limits": "Manage approval limits",
    "admin:templates": "Manage message templates",
    "admin:integrations": "Manage integrations",
    "admin:api_keys": "Manage API keys",
    "admin:billing": "Manage subscription and billing",
    "admin:llm": "Configure the LLM provider",
    "settings:read": "View tenant settings",
    "settings:update": "Update tenant settings",
    "platform:tenants": "Operate all tenants (vendor only)",
    "platform:impersonate": "Impersonate tenant users (audited, time-boxed)",
}

READ_ONLY_REQUESTER = {
    "pr:read", "pr:create", "pr:update", "rfq:read", "supplier:read", "offer:read",
    "comparison:read", "comment:create", "report:read", "settings:read",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "superadmin": set(ALL_PERMISSIONS),
    "admin": set(ALL_PERMISSIONS) - {"platform:tenants", "platform:impersonate"},
    "procurement_manager": {
        "pr:read", "pr:create", "pr:update", "pr:verify", "rfq:read", "rfq:create", "rfq:update",
        "rfq:send", "rfq:cancel", "supplier:read", "supplier:create", "supplier:update",
        "supplier:import", "supplier:discovery", "supplier:approve", "offer:read", "offer:verify",
        "negotiation:read", "negotiation:policy", "negotiation:draft", "negotiation:send",
        "negotiation:auto", "comparison:read", "approval:read", "approval:decide",
        "approval:delegate", "po:create", "po:export", "report:read", "comment:create",
        "audit:read", "admin:templates", "settings:read",
    },
    "buyer": {
        "pr:read", "pr:create", "pr:update", "pr:verify", "rfq:read", "rfq:create", "rfq:update",
        "rfq:send", "supplier:read", "supplier:create", "supplier:update", "supplier:discovery",
        "offer:read", "offer:verify", "negotiation:read", "negotiation:draft", "negotiation:send",
        "comparison:read", "approval:read", "po:create", "report:read", "comment:create",
        "settings:read",
    },
    "requester": READ_ONLY_REQUESTER,
    "finance_approver": {
        "pr:read", "rfq:read", "offer:read", "comparison:read", "approval:read", "approval:decide",
        "approval:delegate", "report:read", "comment:create", "audit:read", "settings:read",
    },
}


def permissions_for(roles: list[str] | tuple[str, ...], is_superadmin: bool = False) -> set[str]:
    perms: set[str] = set()
    if is_superadmin:
        perms |= ROLE_PERMISSIONS["superadmin"]
    for role in roles or []:
        perms |= ROLE_PERMISSIONS.get(role, set())
    return perms


def has_permission(roles: list[str] | tuple[str, ...], permission: str, is_superadmin: bool = False) -> bool:
    return permission in permissions_for(roles, is_superadmin)


@dataclass(frozen=True)
class RoleDef:
    code: str
    name: str
    name_ar: str


def role_definitions() -> list[RoleDef]:
    return [RoleDef(code=c, name=v["name"], name_ar=v["name_ar"]) for c, v in ROLES.items()]
