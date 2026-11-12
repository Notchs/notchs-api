"""Email + password accounts: login with lockout, set password from an emailed link, forgot
password. Shared by venue members and admins; each describes itself with an `AccountKind`.

Emails are not sent from here. Methods that need one return the message, and the router
sends it in the background after the response, so a request for a registered email takes
the same time as one for an unknown email.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.email import EmailMessage
from app.core import rate_limit
from app.core.logging import get_logger
from app.modules.auth.emails import normalize_email
from app.modules.auth.enums import SubjectType, TokenType
from app.modules.auth.exceptions import (
    AccountInactiveException,
    AuthRateLimitedException,
    CurrentPasswordInvalidException,
    InvalidCredentialsException,
    PasswordInvalidException,
    TokenInvalidException,
)
from app.modules.auth.models import AuthToken
from app.modules.auth.one_time_tokens import OneTimeTokenService
from app.modules.auth.passwords import (
    check_password_policy,
    hash_password_async,
    needs_rehash,
    verify_dummy_async,
    verify_password_async,
)
from app.modules.auth.sessions import IssuedSession, SessionService, Subject

logger = get_logger(__name__)

MAX_LOGIN_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60
# Wrong passwords for one account from anywhere: stops guessing from many (or made-up) IPs.
MAX_LOGIN_FAILURES_PER_ACCOUNT = 20
MAX_RESETS_PER_EMAIL_PER_HOUR = 3
MAX_RESETS_PER_IP_PER_HOUR = 10
HOUR = 60 * 60


class PasswordAccount(Protocol):
    """What a login table needs: `venue_members` and `admin_users` both have these."""

    id: uuid.UUID
    email: str
    name: str
    password_hash: str | None
    last_login_at: datetime | None


@dataclass(frozen=True, slots=True)
class AccountKind[AccountT: PasswordAccount]:
    key: str  # in rate-limit keys, e.g. "venue" -> rl:login:venue:<email>:<ip>
    subject_type: SubjectType
    model: type[AccountT]
    load_active: Callable[[AsyncSession, uuid.UUID], Awaitable[AccountT | None]]
    subject_of: Callable[[AccountT], Subject]
    subject_id_of: Callable[[AuthToken], uuid.UUID | None]
    reset_email: Callable[[AccountT, str], EmailMessage]
    changed_email: Callable[[AccountT], EmailMessage]  # "your password was changed"


@dataclass(frozen=True, slots=True)
class PasswordLogin[AccountT: PasswordAccount]:
    account: AccountT
    session: IssuedSession


class PasswordAuthService[AccountT: PasswordAccount]:
    def __init__(self, session: AsyncSession, redis: Redis, kind: AccountKind[AccountT]) -> None:
        self.session = session
        self.redis = redis
        self.kind = kind
        self.sessions = SessionService(session)
        self.tokens = OneTimeTokenService(session)

    async def login(
        self, email: str, password: str, *, ip: str | None, user_agent: str | None
    ) -> PasswordLogin[AccountT]:
        """The same error for an unknown email, a wrong password and too many attempts."""
        email = normalize_email(email)
        failures_key = f"rl:login:{self.kind.key}:{email}:{ip or 'unknown'}"
        account_key = f"rl:login:{self.kind.key}:{email}:any"
        if await rate_limit.is_limited(
            self.redis, failures_key, limit=MAX_LOGIN_FAILURES
        ) or await rate_limit.is_limited(
            self.redis, account_key, limit=MAX_LOGIN_FAILURES_PER_ACCOUNT
        ):
            await verify_dummy_async(password)
            raise InvalidCredentialsException()

        account = await self._by_email(email)
        if account is None or account.password_hash is None:
            await verify_dummy_async(password)  # same time as a real check
            await self._count_failure(failures_key)
            await self._count_account_failure(account_key)
            raise InvalidCredentialsException()
        if not await verify_password_async(account.password_hash, password):
            await self._count_failure(failures_key)
            await self._count_account_failure(account_key)
            raise InvalidCredentialsException()

        # Only someone who knows the password learns that the account is switched off.
        if await self.kind.load_active(self.session, account.id) is None:
            raise AccountInactiveException()

        await rate_limit.reset(self.redis, failures_key)
        if needs_rehash(account.password_hash):
            account.password_hash = await hash_password_async(password)
        return await self._log_in(account, ip=ip, user_agent=user_agent)

    async def set_password(
        self, raw_token: str, password: str, *, ip: str | None, user_agent: str | None
    ) -> PasswordLogin[AccountT]:
        """First password or a reset, from an emailed link. Logs in and ends every other
        session, since an old password may be known to someone else."""
        token = await self.tokens.consume(
            raw_token, (TokenType.SET_PASSWORD, TokenType.PASSWORD_RESET), self.kind.subject_type
        )
        account_id = self.kind.subject_id_of(token)
        account = await self.kind.load_active(self.session, account_id) if account_id else None
        if account is None:
            raise TokenInvalidException()

        check_password_policy(password, email=account.email)
        account.password_hash = await hash_password_async(password)
        await self.sessions.end_all(self.kind.subject_of(account))
        return await self._log_in(account, ip=ip, user_agent=user_agent)

    async def forgot_password(self, email: str, *, ip: str | None) -> EmailMessage | None:
        """The reset email to send, or None when there is no such active account.

        The caller answers the same way in both cases.
        """
        email = normalize_email(email)
        if not await rate_limit.hit(
            self.redis,
            f"rl:reset:{self.kind.key}:ip:{ip or 'unknown'}",
            limit=MAX_RESETS_PER_IP_PER_HOUR,
            window_seconds=HOUR,
        ) or not await rate_limit.hit(
            self.redis,
            f"rl:reset:{self.kind.key}:email:{email}",
            limit=MAX_RESETS_PER_EMAIL_PER_HOUR,
            window_seconds=HOUR,
        ):
            raise AuthRateLimitedException()

        account = await self._by_email(email)
        if account is None or await self.kind.load_active(self.session, account.id) is None:
            logger.info("Password reset asked for an unknown or inactive %s email", self.kind.key)
            return None
        raw = await self.tokens.issue(
            self.kind.subject_of(account), TokenType.PASSWORD_RESET, ip=ip
        )
        return self.kind.reset_email(account, raw)

    async def verify_current_password(self, account: AccountT, password: str) -> None:
        """For changes made while logged in (password, email). 5 wrong tries in 15 minutes
        block further tries until the window ends."""
        key = f"rl:reauth:{self.kind.key}:{account.id}"
        if await rate_limit.is_limited(self.redis, key, limit=MAX_LOGIN_FAILURES):
            raise AuthRateLimitedException("Too many wrong passwords. Try again in 15 minutes.")
        if account.password_hash is None or not await verify_password_async(
            account.password_hash, password
        ):
            await self._count_failure(key)
            raise CurrentPasswordInvalidException()
        await rate_limit.reset(self.redis, key)

    async def change_password(
        self,
        account: AccountT,
        current_password: str,
        new_password: str,
        *,
        ip: str | None,
        user_agent: str | None,
    ) -> tuple[PasswordLogin[AccountT], EmailMessage]:
        """Change the password while logged in. Every session ends and this device gets a new
        one, so it stays logged in while other devices are logged out. Returns the new session
        and the "password changed" notice to send."""
        await self.verify_current_password(account, current_password)
        check_password_policy(new_password, email=account.email)
        if current_password == new_password:
            raise PasswordInvalidException("Choose a password different from the current one.")
        account.password_hash = await hash_password_async(new_password)
        await self.sessions.end_all(self.kind.subject_of(account))
        login = await self._log_in(account, ip=ip, user_agent=user_agent)
        return login, self.kind.changed_email(account)

    async def issue_set_password_token(self, account: AccountT) -> str:
        """Raw token for a "set your password" link (new venue, new admin)."""
        return await self.tokens.issue(self.kind.subject_of(account), TokenType.SET_PASSWORD)

    async def _by_email(self, email: str) -> AccountT | None:
        model: Any = self.kind.model
        return await self.session.scalar(select(model).where(model.email == email))

    async def _log_in(
        self, account: AccountT, *, ip: str | None, user_agent: str | None
    ) -> PasswordLogin[AccountT]:
        account.last_login_at = datetime.now(UTC)
        issued = await self.sessions.start(
            self.kind.subject_of(account), ip=ip, user_agent=user_agent
        )
        return PasswordLogin(account=account, session=issued)

    async def _count_account_failure(self, key: str) -> None:
        await rate_limit.hit(
            self.redis,
            key,
            limit=MAX_LOGIN_FAILURES_PER_ACCOUNT,
            window_seconds=LOGIN_WINDOW_SECONDS,
        )

    async def _count_failure(self, key: str) -> None:
        await rate_limit.hit(
            self.redis, key, limit=MAX_LOGIN_FAILURES, window_seconds=LOGIN_WINDOW_SECONDS
        )
