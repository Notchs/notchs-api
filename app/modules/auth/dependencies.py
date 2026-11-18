"""Who is calling: FastAPI dependencies that read the access cookie.

    @router.get("/venue/me")
    async def me(member: CurrentVenueMember) -> ...

Each one verifies the JWT in that audience's access cookie and loads the account. Missing,
expired or tampered cookies give 401 `auth.not_authenticated`; a deactivated account gives
403 `auth.account_inactive`.

The cookies are declared as security schemes (`APIKeyCookie`), so the API docs show a lock and
which login each endpoint needs. That only describes them: the cookie is read exactly as before.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Security
from fastapi.security import APIKeyCookie

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


def _login_cookie(subject_type: SubjectType, who: str, how: str) -> APIKeyCookie:
    # auto_error=False: a missing cookie is our own 401 `auth.not_authenticated`, not FastAPI's.
    return APIKeyCookie(
        name=access_cookie_name(subject_type),
        scheme_name=f"{who} login",
        description=f"Set by logging in: {how}. The browser sends it by itself.",
        auto_error=False,
    )


ADMIN_LOGIN = _login_cookie(SubjectType.ADMIN, "Admin", "POST /api/v1/admin/auth/login")
VENUE_LOGIN = _login_cookie(SubjectType.VENUE_MEMBER, "Venue", "POST /api/v1/venue/auth/login")
CUSTOMER_LOGIN = _login_cookie(
    SubjectType.USER, "Customer", "POST /api/v1/auth/customer/consume with a WhatsApp link token"
)


def access_claims(token: str | None, subject_type: SubjectType) -> AccessClaims:
    if not token:
        raise NotAuthenticatedException()
    return decode_access_token(token, expected_type=subject_type)


async def _load[ModelT](
    token: str | None,
    session: DBSession,
    subject_type: SubjectType,
    load_active: Callable[[Any, uuid.UUID], Awaitable[ModelT | None]],
) -> ModelT:
    claims = access_claims(token, subject_type)
    account = await load_active(session, claims.subject_id)
    if account is None:
        raise AccountInactiveException()
    return account


async def current_venue_member(
    session: DBSession, token: Annotated[str | None, Security(VENUE_LOGIN)]
) -> VenueMember:
    return await _load(token, session, SubjectType.VENUE_MEMBER, active_venue_member)


async def current_admin(
    session: DBSession, token: Annotated[str | None, Security(ADMIN_LOGIN)]
) -> AdminUser:
    return await _load(token, session, SubjectType.ADMIN, active_admin)


async def current_user(
    session: DBSession, token: Annotated[str | None, Security(CUSTOMER_LOGIN)]
) -> User:
    return await _load(token, session, SubjectType.USER, active_user)


def get_session_service(session: DBSession) -> SessionService:
    return SessionService(session)


async def current_full_admin(admin: Annotated[AdminUser, Depends(current_admin)]) -> AdminUser:
    """An admin with the `admin` role. Reviewers (proof review) get 403 here."""
    if admin.access != AdminRole.ADMIN:
        raise AdminRoleRequiredException()
    return admin


CurrentVenueMember = Annotated[VenueMember, Depends(current_venue_member)]
CurrentAdmin = Annotated[AdminUser, Depends(current_admin)]
CurrentFullAdmin = Annotated[AdminUser, Depends(current_full_admin)]
CurrentUser = Annotated[User, Depends(current_user)]
SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
