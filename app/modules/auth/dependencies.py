"""Who is calling: FastAPI dependencies that read the access cookie.

    @router.get("/venue/me")
    async def me(member: CurrentVenueMember) -> ...

Each one verifies the JWT in that audience's access cookie and loads the account. Missing,
expired or tampered cookies give 401 `auth.not_authenticated`; a deactivated account gives
403 `auth.account_inactive`.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Request

from app.common.dependencies import DBSession
from app.modules.admin_users.models import AdminRole, AdminUser
from app.modules.auth.access_tokens import AccessClaims, decode_access_token
from app.modules.auth.cookies import access_cookie_name
from app.modules.auth.enums import SubjectType
from app.modules.auth.exceptions import (
    AccountInactiveException,
    AdminRoleRequiredException,
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


async def current_user_optional(request: Request, session: DBSession) -> User | None:
    """The logged-in customer, or None for anyone else. For public pages that add a personal
    touch when they can (e.g. a shared card hides places the viewer already notched)."""
    if not request.cookies.get(access_cookie_name(SubjectType.USER)):
        return None
    try:
        return await current_user(request, session)
    except NotAuthenticatedException, AccountInactiveException:
        return None


def get_session_service(session: DBSession) -> SessionService:
    return SessionService(session)


async def current_full_admin(admin: Annotated[AdminUser, Depends(current_admin)]) -> AdminUser:
    """An admin with the `admin` role. Reviewers (proof review) get 403 here."""
    if admin.role != AdminRole.ADMIN:
        raise AdminRoleRequiredException()
    return admin


CurrentVenueMember = Annotated[VenueMember, Depends(current_venue_member)]
CurrentAdmin = Annotated[AdminUser, Depends(current_admin)]
CurrentFullAdmin = Annotated[AdminUser, Depends(current_full_admin)]
CurrentUser = Annotated[User, Depends(current_user)]
OptionalUser = Annotated[User | None, Depends(current_user_optional)]
SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
