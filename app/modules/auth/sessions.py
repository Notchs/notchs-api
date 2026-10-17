"""Login sessions: a short-lived access JWT plus a rotating refresh token (auth.md §5).

Refresh tokens are stored hashed in `auth_tokens` (token_type `refresh`). Every refresh marks
the old token used and issues a new one in the same `family_id`. If an already-used token is
presented again later, someone else has a copy of it: the whole family is revoked.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.admin_users.models import AdminUser
from app.modules.auth.access_tokens import create_access_token
from app.modules.auth.enums import SubjectType, TokenType
from app.modules.auth.exceptions import NotAuthenticatedException
from app.modules.auth.models import AuthToken
from app.modules.auth.tokens import hash_token, lifetime, new_token
from app.modules.users.models import User, UserStatus
from app.modules.venues.models import Venue, VenueMember

logger = get_logger(__name__)

# Two browser tabs can refresh with the same cookie at almost the same time. A reuse inside
# this window is treated as that race (the request just fails), not as a stolen token.
REUSE_GRACE = timedelta(seconds=10)

SUBJECT_COLUMN = {
    SubjectType.USER: AuthToken.user_id,
    SubjectType.VENUE_MEMBER: AuthToken.venue_member_id,
    SubjectType.ADMIN: AuthToken.admin_user_id,
}


@dataclass(frozen=True, slots=True)
class Subject:
    type: SubjectType
    id: uuid.UUID
    # Venue members only: carried in the access token so venue routes know the venue.
    venue_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class IssuedSession:
    subject: Subject
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


async def active_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.scalar(
        select(User).where(
            User.id == user_id, User.status == UserStatus.ACTIVE, User.deleted_at.is_(None)
        )
    )


async def active_venue_member(session: AsyncSession, member_id: uuid.UUID) -> VenueMember | None:
    """The member, if it is active and its venue has not been deleted."""
    return await session.scalar(
        select(VenueMember)
        .join(Venue, Venue.id == VenueMember.venue_id)
        .where(
            VenueMember.id == member_id,
            VenueMember.is_active.is_(True),
            Venue.deleted_at.is_(None),
        )
    )


async def active_admin(session: AsyncSession, admin_id: uuid.UUID) -> AdminUser | None:
    return await session.scalar(
        select(AdminUser).where(AdminUser.id == admin_id, AdminUser.is_active.is_(True))
    )


class SessionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(
        self, subject: Subject, *, ip: str | None = None, user_agent: str | None = None
    ) -> IssuedSession:
        """A new login: a fresh refresh-token family."""
        return await self._issue(subject, uuid.uuid4(), ip=ip, user_agent=user_agent)

    async def refresh(
        self,
        raw_refresh_token: str,
        subject_type: SubjectType,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedSession:
        """Swap a refresh token for a new session. Raises NotAuthenticated if it can't be used."""
        now = datetime.now(UTC)
        token = await self.session.scalar(
            select(AuthToken)
            .where(
                AuthToken.token_hash == hash_token(raw_refresh_token),
                AuthToken.token_type == TokenType.REFRESH,
            )
            .with_for_update()
        )
        if token is None or token.subject_type != subject_type or token.revoked_at is not None:
            raise NotAuthenticatedException()

        if token.used_at is not None:
            if now - token.used_at > REUSE_GRACE:
                logger.warning(
                    "Reused refresh token: revoking session family %s (%s)",
                    token.family_id,
                    token.subject_type,
                )
                await self._revoke_family(token.family_id, now)
                # Keep the revocation even though this request fails.
                await self.session.commit()
            raise NotAuthenticatedException()

        if token.expires_at <= now:
            raise NotAuthenticatedException()

        subject = await self._active_subject(token)
        if subject is None:
            await self._revoke_family(token.family_id, now)
            await self.session.commit()
            raise NotAuthenticatedException()

        token.used_at = now
        return await self._issue(subject, token.family_id, ip=ip, user_agent=user_agent)

    async def end(self, raw_refresh_token: str, subject_type: SubjectType) -> None:
        """Logout: revoke the session family of this refresh token. Unknown tokens are ignored."""
        token = await self.session.scalar(
            select(AuthToken).where(
                AuthToken.token_hash == hash_token(raw_refresh_token),
                AuthToken.token_type == TokenType.REFRESH,
                AuthToken.subject_type == subject_type,
            )
        )
        if token is not None:
            await self._revoke_family(token.family_id, datetime.now(UTC))

    async def end_all(self, subject: Subject, *, keep_family: uuid.UUID | None = None) -> int:
        """Revoke every session of `subject`, except `keep_family` (the one in use, if given).

        Used by password change (keep the current session) and reset or deactivation (keep none).
        """
        query = update(AuthToken).where(
            SUBJECT_COLUMN[subject.type] == subject.id,
            AuthToken.token_type == TokenType.REFRESH,
            AuthToken.revoked_at.is_(None),
        )
        if keep_family is not None:
            query = query.where(AuthToken.family_id != keep_family)
        result = await self.session.execute(query.values(revoked_at=datetime.now(UTC)))
        return result.rowcount or 0

    async def _issue(
        self,
        subject: Subject,
        family_id: uuid.UUID | None,
        *,
        ip: str | None,
        user_agent: str | None,
    ) -> IssuedSession:
        now = datetime.now(UTC)
        raw = new_token()
        refresh_expires_at = now + lifetime(TokenType.REFRESH)
        self.session.add(
            AuthToken(
                subject_type=subject.type,
                **{SUBJECT_COLUMN[subject.type].key: subject.id},
                token_type=TokenType.REFRESH,
                token_hash=hash_token(raw),
                expires_at=refresh_expires_at,
                family_id=family_id,
                ip_address=ip,
                user_agent=user_agent,
            )
        )
        await self.session.flush()
        return IssuedSession(
            subject=subject,
            access_token=create_access_token(
                subject.type, subject.id, venue_id=subject.venue_id, now=now
            ),
            access_expires_at=now + timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES),
            refresh_token=raw,
            refresh_expires_at=refresh_expires_at,
        )

    async def _active_subject(self, token: AuthToken) -> Subject | None:
        match token.subject_type:
            case SubjectType.USER:
                user = await active_user(self.session, token.user_id)
                return Subject(SubjectType.USER, user.id) if user else None
            case SubjectType.VENUE_MEMBER:
                member = await active_venue_member(self.session, token.venue_member_id)
                return (
                    Subject(SubjectType.VENUE_MEMBER, member.id, venue_id=member.venue_id)
                    if member
                    else None
                )
            case SubjectType.ADMIN:
                admin = await active_admin(self.session, token.admin_user_id)
                return Subject(SubjectType.ADMIN, admin.id) if admin else None
        return None

    async def _revoke_family(self, family_id: uuid.UUID | None, now: datetime) -> None:
        if family_id is None:
            return
        await self.session.execute(
            update(AuthToken)
            .where(AuthToken.family_id == family_id, AuthToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
