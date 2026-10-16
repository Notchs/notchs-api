"""Venue member accounts: login, set/forgot/change password (mechanics in password_auth.py),
plus what is venue-specific: the venue itself, the welcome email and changing the login email."""

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.email import EmailMessage
from app.core.config import settings
from app.modules.auth import messages
from app.modules.auth.emails import is_disposable, normalize_email
from app.modules.auth.enums import OtpPurpose, SubjectType
from app.modules.auth.exceptions import (
    AccountInactiveException,
    CodeInvalidException,
    EmailNotAllowedException,
    EmailTakenException,
    EmailUnchangedException,
)
from app.modules.auth.otp import CODE_TTL_SECONDS, EmailCodes
from app.modules.auth.password_auth import (
    MAX_LOGIN_FAILURES,
    AccountKind,
    PasswordAuthService,
    PasswordLogin,
)
from app.modules.auth.sessions import IssuedSession, Subject, active_venue_member
from app.modules.venues.models import Venue, VenueMember

__all__ = ["MAX_LOGIN_FAILURES", "LoggedIn", "VenueAuthService", "venue_subject"]

EMAIL_CHANGE_CODE_PURPOSE = "confirm the new login email for your Notchs venue"


def venue_subject(member: VenueMember) -> Subject:
    return Subject(SubjectType.VENUE_MEMBER, member.id, venue_id=member.venue_id)


VENUE_MEMBERS = AccountKind(
    key="venue",
    subject_type=SubjectType.VENUE_MEMBER,
    model=VenueMember,
    load_active=active_venue_member,
    subject_of=venue_subject,
    subject_id_of=lambda token: token.venue_member_id,
    reset_email=lambda member, raw: messages.password_reset_email(
        to=member.email, name=member.name, raw_token=raw, app_url=settings.VENUE_APP_URL
    ),
    changed_email=lambda member: messages.password_changed_email(
        to=member.email, name=member.name, app_url=settings.VENUE_APP_URL
    ),
)

# The new address waiting for its code, per member. Same lifetime as the code.
PENDING_EMAIL_TTL_SECONDS = CODE_TTL_SECONDS


def _pending_key(member: VenueMember) -> str:
    return f"emailchange:{member.id}"


@dataclass(frozen=True, slots=True)
class LoggedIn:
    member: VenueMember
    venue: Venue
    session: IssuedSession


class VenueAuthService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis
        self.passwords = PasswordAuthService(session, redis, VENUE_MEMBERS)
        self.codes = EmailCodes(redis)

    async def login(
        self, email: str, password: str, *, ip: str | None, user_agent: str | None
    ) -> LoggedIn:
        return await self._with_venue(
            await self.passwords.login(email, password, ip=ip, user_agent=user_agent)
        )

    async def set_password(
        self, raw_token: str, password: str, *, ip: str | None, user_agent: str | None
    ) -> LoggedIn:
        return await self._with_venue(
            await self.passwords.set_password(raw_token, password, ip=ip, user_agent=user_agent)
        )

    async def forgot_password(self, email: str, *, ip: str | None) -> EmailMessage | None:
        return await self.passwords.forgot_password(email, ip=ip)

    async def change_password(
        self,
        member: VenueMember,
        current_password: str,
        new_password: str,
        *,
        ip: str | None,
        user_agent: str | None,
    ) -> tuple[LoggedIn, EmailMessage]:
        login, notice = await self.passwords.change_password(
            member, current_password, new_password, ip=ip, user_agent=user_agent
        )
        return await self._with_venue(login), notice

    async def request_email_change(
        self, member: VenueMember, new_email: str, current_password: str, *, ip: str | None
    ) -> EmailMessage | None:
        """Step 1: check the password and email a code to the NEW address. Returns None (and
        sends nothing) when another login already uses that address, so the answer never
        reveals who is registered."""
        await self.passwords.verify_current_password(member, current_password)
        new_email = normalize_email(new_email)
        if new_email == member.email:
            raise EmailUnchangedException()
        if is_disposable(new_email):
            raise EmailNotAllowedException()
        if await self._email_taken(new_email):
            return None
        code = await self.codes.issue(OtpPurpose.EMAIL_CHANGE, new_email, ip=ip or "unknown")
        await self.redis.set(_pending_key(member), new_email, ex=PENDING_EMAIL_TTL_SECONDS)
        return messages.email_code_email(to=new_email, code=code, purpose=EMAIL_CHANGE_CODE_PURPOSE)

    async def confirm_email_change(self, member: VenueMember, code: str) -> EmailMessage:
        """Step 2: check the code and switch the login email. Sessions stay. Returns the notice
        for the OLD address."""
        new_email = await self.redis.get(_pending_key(member))
        if new_email is None:
            raise CodeInvalidException()
        await self.codes.verify(OtpPurpose.EMAIL_CHANGE, new_email, code)
        if await self._email_taken(new_email):
            raise EmailTakenException()

        old_email = member.email
        member.email = new_email
        try:
            async with self.session.begin_nested():  # unique email: the final guard
                await self.session.flush()
        except IntegrityError as exc:
            raise EmailTakenException() from exc
        await self.redis.delete(_pending_key(member))
        return messages.email_changed_email(to=old_email, name=member.name, new_email=new_email)

    async def welcome_email(self, member: VenueMember, venue: Venue) -> EmailMessage:
        """Set-password link for a newly approved venue."""
        raw = await self.passwords.issue_set_password_token(member)
        return messages.venue_welcome_email(
            to=member.email, name=member.name, venue_name=venue.name, raw_token=raw
        )

    async def venue_of(self, member: VenueMember) -> Venue:
        venue = await self.session.get(Venue, member.venue_id)
        if venue is None:  # the FK makes this impossible; keeps the type checker honest
            raise AccountInactiveException()
        return venue

    async def _email_taken(self, email: str) -> bool:
        return bool(await self.session.scalar(select(exists().where(VenueMember.email == email))))

    async def _with_venue(self, login: PasswordLogin[VenueMember]) -> LoggedIn:
        return LoggedIn(
            member=login.account, venue=await self.venue_of(login.account), session=login.session
        )
