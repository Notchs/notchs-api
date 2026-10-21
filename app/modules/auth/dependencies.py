"""Who is calling: FastAPI dependencies that read the access cookie.

    @router.get("/venue/me")
    async def me(member: CurrentVenueMember) -> ...

Each one verifies the JWT in that audience's access cookie and loads the account. Missing,
expired or tampered cookies give 401 `auth.not_authenticated`; a deactivated account gives
403 `auth.account_inactive`.

Admin endpoints also need a permission from the admin's role:

    CanReview = Annotated[AdminUser, Depends(require_permission(Permission.APPLICATIONS_REVIEW))]

    @router.post("/{ref}/approve")
    async def approve(admin: CanReview) -> ...

Without it: 403 `auth.permission_required`, with the permission in `details`.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dependencies import DBSession
from app.modules.admin_users.models import AdminUser
from app.modules.admin_users.permissions import Permission
from app.modules.admin_users.roles import permissions_of
from app.modules.auth.access_tokens import AccessClaims, decode_access_token
from app.modules.auth.cookies import access_cookie_name
from app.modules.auth.enums import SubjectType
from app.modules.auth.exceptions import (
    AccountInactiveException,
    AdminPermissionRequiredException,
    NotAuthenticatedException,
)
from app.modules.auth.sessions import (
    SessionService,
    active_admin,
    active_user,
    active_venue_member,
)
from app.modules.users.models import User
from app.modules.venues.models import VenueMember


def access_claims(request: Request, subject_type: SubjectType) -> AccessClaims:
    token = request.cookies.get(access_cookie_name(subject_type))
    if not token:
        raise NotAuthenticatedException()
    return decode_access_token(token, expected_type=subject_type)


async def _load[ModelT](
    request: Request,
    session: DBSession,
    subject_type: SubjectType,
    load_active: Callable[[Any, uuid.UUID], Awaitable[ModelT | None]],
) -> ModelT:
    claims = access_claims(request, subject_type)
    account = await load_active(session, claims.subject_id)
    if account is None:
        raise AccountInactiveException()
    return account


async def current_venue_member(request: Request, session: DBSession) -> VenueMember:
    return await _load(request, session, SubjectType.VENUE_MEMBER, active_venue_member)


async def current_admin(request: Request, session: DBSession) -> AdminUser:
    return await _load(request, session, SubjectType.ADMIN, active_admin)


async def current_user(request: Request, session: DBSession) -> User:
    return await _load(request, session, SubjectType.USER, active_user)


def get_session_service(session: DBSession) -> SessionService:
    return SessionService(session)


def require_permission(
    permission: Permission,
) -> Callable[[AdminUser, AsyncSession], Awaitable[AdminUser]]:
    """A dependency: the logged-in admin, if their role has `permission`."""

    async def dependency(
        admin: Annotated[AdminUser, Depends(current_admin)], session: DBSession
    ) -> AdminUser:
        _, granted = await permissions_of(session, admin)
        if permission not in granted:
            raise AdminPermissionRequiredException(details={"permission": permission.value})
        return admin

    return dependency


CurrentVenueMember = Annotated[VenueMember, Depends(current_venue_member)]
CurrentAdmin = Annotated[AdminUser, Depends(current_admin)]
CurrentUser = Annotated[User, Depends(current_user)]
SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
