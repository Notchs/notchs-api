"""Customer login: a single-use link sent by the WhatsApp bot (docs/modules/customers.md §4.3).

The customer sends LOGIN on WhatsApp; the bot calls `issue_login_link` and replies with the
link; the customer site's /login/verify page POSTs the token to `/auth/customer/consume`.
Opening the link alone changes nothing, because WhatsApp link previews fetch it too.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.config import settings
from app.modules.auth.enums import SubjectType, TokenType
from app.modules.auth.exceptions import AuthRateLimitedException, TokenInvalidException
from app.modules.auth.one_time_tokens import OneTimeTokenService
from app.modules.auth.sessions import IssuedSession, SessionService, Subject, active_user
from app.modules.users.models import User

MAX_LINKS_PER_WINDOW = 3
LINK_WINDOW_SECONDS = 15 * 60


def user_subject(user: User) -> Subject:
    return Subject(SubjectType.USER, user.id)


def login_link(raw_token: str) -> str:
    return f"{settings.CUSTOMER_APP_URL}/login/verify?{urlencode({'token': raw_token})}"


@dataclass(frozen=True, slots=True)
class CustomerLogin:
    user: User
    session: IssuedSession


class CustomerAuthService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis
        self.tokens = OneTimeTokenService(session)
        self.sessions = SessionService(session)

    async def issue_login_link(self, user: User) -> str:
        """For the WhatsApp bot's reply to LOGIN. A new link cancels the older one.

        Raises AuthRateLimited after 3 links in 15 minutes; raises TokenInvalid for a blocked
        or deleted customer, so the bot never sends them a link.
        """
        if await active_user(self.session, user.id) is None:
            raise TokenInvalidException()
        if not await rate_limit.hit(
            self.redis,
            f"rl:magic:{user.id}",
            limit=MAX_LINKS_PER_WINDOW,
            window_seconds=LINK_WINDOW_SECONDS,
        ):
            raise AuthRateLimitedException("Too many login links. Try again in 15 minutes.")
        raw = await self.tokens.issue(user_subject(user), TokenType.MAGIC_LINK)
        return login_link(raw)

    async def consume(
        self, raw_token: str, *, ip: str | None, user_agent: str | None
    ) -> CustomerLogin:
        """Use the link once and start a session. Blocked and deleted customers get the same
        "invalid or expired" error as a bad link."""
        token = await self.tokens.consume(raw_token, (TokenType.MAGIC_LINK,), SubjectType.USER)
        user = await active_user(self.session, token.user_id) if token.user_id else None
        if user is None:
            raise TokenInvalidException()
        user.last_seen_at = datetime.now(UTC)
        issued = await self.sessions.start(user_subject(user), ip=ip, user_agent=user_agent)
        return CustomerLogin(user=user, session=issued)
