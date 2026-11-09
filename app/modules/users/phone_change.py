"""Moving a customer's account to a new WhatsApp number (team decision Q16), for free.

    website   POST /me/phone-change -> a 6-digit code and a WhatsApp link pre-typed with
              "CHANGE 123456" (10 minutes, one live code per customer)
    new phone sends "CHANGE 123456" to Notchs -> the account moves to that number

Sending from the new number proves the customer has it, and the bot's answer is a reply to
their own message, so it costs nothing. Codes are stored hashed and work once. A number that
already has a Notchs account is refused: two accounts are never merged.
"""

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from urllib.parse import quote

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.auth.tokens import hash_token
from app.modules.users.models import User, UserStatus

logger = get_logger(__name__)

CODE_TTL = timedelta(minutes=10)
MAX_CODES_PER_WINDOW = 3  # per customer
MAX_TRIES_PER_WINDOW = 5  # wrong codes per sending number
WINDOW_SECONDS = 15 * 60
COMMAND = "CHANGE"
_COMMAND = re.compile(rf"^\s*{COMMAND}\s+([0-9]{{6}})\s*$", re.IGNORECASE)


class PhoneChangeOutcome(StrEnum):
    CHANGED = "changed"
    INVALID = "invalid"  # wrong, used or expired code
    SAME = "same"  # already this account's number
    TAKEN = "taken"  # the number has its own Notchs account
    LIMITED = "limited"  # too many wrong codes from this number


@dataclass(frozen=True, slots=True)
class PhoneChangeStart:
    code: str
    expires_at: datetime
    whatsapp_link: str | None  # None until WHATSAPP_PUBLIC_NUMBER is set


def parse_command(text: str | None) -> str | None:
    """The code in "CHANGE 123456", or None if the message isn't one."""
    match = _COMMAND.match(text or "")
    return match.group(1) if match else None


def _code_key(code: str) -> str:
    return f"pc:code:{hash_token(code)}"


def _user_key(user_id: uuid.UUID) -> str:
    return f"pc:user:{user_id}"


class PhoneChangeService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis

    async def start(self, user: User) -> PhoneChangeStart | None:
        """A new code (the previous one stops working), or None if asked for too often."""
        if not await rate_limit.hit(
            self.redis,
            f"rl:phone_change:start:{user.id}",
            limit=MAX_CODES_PER_WINDOW,
            window_seconds=WINDOW_SECONDS,
        ):
            return None
        previous = await self.redis.getdel(_user_key(user.id))
        if previous:
            await self.redis.delete(f"pc:code:{previous}")
        ttl = int(CODE_TTL.total_seconds())
        while True:  # another customer's live code is never reused
            code = f"{secrets.randbelow(10**6):06d}"
            if await self.redis.set(_code_key(code), str(user.id), ex=ttl, nx=True):
                break
        await self.redis.set(_user_key(user.id), hash_token(code), ex=ttl)
        number = settings.WHATSAPP_PUBLIC_NUMBER
        link = (
            f"https://wa.me/{number}?text={quote(f'{COMMAND} {code}', safe='')}" if number else None
        )
        return PhoneChangeStart(code, datetime.now(UTC) + CODE_TTL, link)

    async def finish(self, new_phone: str, code: str) -> tuple[PhoneChangeOutcome, User | None]:
        """Use a code sent from `new_phone`. Runs in the bot's transaction."""
        tries = f"rl:phone_change:try:{new_phone}"
        if await rate_limit.is_limited(self.redis, tries, limit=MAX_TRIES_PER_WINDOW):
            return PhoneChangeOutcome.LIMITED, None
        user_id = await self.redis.getdel(_code_key(code))  # works once
        user = None
        if user_id is not None:
            user = await self.session.scalar(
                select(User).where(User.id == uuid.UUID(user_id)).with_for_update()
            )
        if user is None or user.status != UserStatus.ACTIVE or user.deleted_at is not None:
            await rate_limit.hit(
                self.redis, tries, limit=MAX_TRIES_PER_WINDOW, window_seconds=WINDOW_SECONDS
            )
            return PhoneChangeOutcome.INVALID, None
        await self.redis.delete(_user_key(user.id))
        if user.phone_e164 == new_phone:
            return PhoneChangeOutcome.SAME, user
        taken = await self.session.scalar(select(User.id).where(User.phone_e164 == new_phone))
        if taken is not None:
            return PhoneChangeOutcome.TAKEN, user
        try:
            async with self.session.begin_nested():
                user.phone_e164 = new_phone
                await self.session.flush()
        except IntegrityError:  # the number signed up at the same moment
            return PhoneChangeOutcome.TAKEN, user
        logger.info("Customer %s moved to a new WhatsApp number", user.id)
        return PhoneChangeOutcome.CHANGED, user
