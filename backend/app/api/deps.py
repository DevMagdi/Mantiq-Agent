"""Request context, authentication and the single RBAC enforcement layer."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden, Unauthorized
from app.core.rbac import permissions_for
from app.core.security import decode_token
from app.db.models.entities import Tenant, User
from app.db.session import get_session


@dataclass
class Context:
    """Everything a route handler needs about the caller."""

    user: User
    tenant: Tenant
    session: AsyncSession
    request: Request
    permissions: set[str] = field(default_factory=set)

    @property
    def tenant_id(self) -> str:
        return self.tenant.id

    @property
    def user_id(self) -> str:
        return self.user.id

    @property
    def request_id(self) -> str:
        return getattr(self.request.state, "request_id", "")

    @property
    def ip(self) -> str:
        fwd = self.request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return self.request.client.host if self.request.client else ""

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise Forbidden(f"Missing permission: {permission}", extra={"required_permission": permission})


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    raise Unauthorized("Missing bearer token.")


async def get_session_dep() -> AsyncIterator[AsyncSession]:
    async for s in get_session():
        yield s


async def current_context(
    request: Request,
    session: AsyncSession = Depends(get_session_dep),
) -> Context:
    token = _bearer(request)
    try:
        payload = decode_token(token, "access")
    except ValueError as exc:
        raise Unauthorized("Session expired or invalid. Please sign in again.") from exc

    user = (await session.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if not user or not user.is_active:
        raise Unauthorized("This account is not active.")
    tenant = (await session.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one_or_none()
    if not tenant:
        raise Unauthorized("Tenant not found.")

    # Bind tenant context for PostgreSQL RLS (NFR-SEC / AC tenant-isolation).
    session.info["tenant_id"] = tenant.id
    request.state.tenant_id = tenant.id
    request.state.user_id = user.id
    request.state.tenant = tenant
    request.state.user = user

    roles = list(user.roles or [])
    perms = permissions_for(roles, user.is_superadmin)
    return Context(user=user, tenant=tenant, session=session, request=request, permissions=perms)


def require(*permissions: str):
    """Dependency factory enforcing one or more permissions (all required)."""

    async def _dep(ctx: Context = Depends(current_context)) -> Context:
        for permission in permissions:
            ctx.require(permission)
        return ctx

    return _dep


async def platform_context(ctx: Context = Depends(current_context)) -> Context:
    """Only the vendor SuperAdmin may operate across tenants."""
    if not ctx.user.is_superadmin:
        raise Forbidden("Platform operator access required.")
    return ctx


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"
