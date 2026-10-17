"""Emailed 6-digit codes that prove someone owns an address (docs/modules/auth.md §3).

Nothing is stored in Postgres. Redis keys:

    otp:{purpose}:{email}        hash: code hash + wrong attempts, 10 min
    otp:cd:{purpose}:{email}     resend cooldown, 60 s
    rl:otp:email:{email}         codes sent to this address in the last hour
    rl:otp:ip:{ip}               codes requested from this IP in the last hour
    emv:{token hash}             verified email, for the application form, 30 min, single use
"""

import hashlib
import hmac
import secrets

from redis.asyncio import Redis

from app.core import rate_limit
from app.modules.auth.emails import normalize_email
from app.modules.auth.enums import OtpPurpose
from app.modules.auth.exceptions import AuthRateLimitedException, CodeInvalidException
from app.modules.auth.tokens import hash_token, new_token

CODE_DIGITS = 6
CODE_TTL_SECONDS = 10 * 60
MAX_WRONG_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
MAX_CODES_PER_EMAIL_PER_HOUR = 5
MAX_CODES_PER_IP_PER_HOUR = 10
VERIFICATION_TOKEN_TTL_SECONDS = 30 * 60
HOUR = 60 * 60


def _code_key(purpose: OtpPurpose, email: str) -> str:
    return f"otp:{purpose.value}:{email}"


def _hash_code(purpose: OtpPurpose, email: str, code: str) -> str:
    return hashlib.sha256(f"{purpose.value}:{email}:{code}".encode()).hexdigest()


def new_code() -> str:
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


class EmailCodes:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def issue(self, purpose: OtpPurpose, email: str, *, ip: str) -> str:
        """Create a code for `email` and return it for sending. A new code replaces the old one.

        Raises AuthRateLimited during the resend cooldown or past the hourly limits.
        """
        email = normalize_email(email)
        cooldown_key = f"otp:cd:{purpose.value}:{email}"
        if not await self.redis.set(cooldown_key, "1", ex=RESEND_COOLDOWN_SECONDS, nx=True):
            raise AuthRateLimitedException("Please wait a minute before asking for a new code.")
        if not await rate_limit.hit(
            self.redis, f"rl:otp:ip:{ip}", limit=MAX_CODES_PER_IP_PER_HOUR, window_seconds=HOUR
        ) or not await rate_limit.hit(
            self.redis,
            f"rl:otp:email:{email}",
            limit=MAX_CODES_PER_EMAIL_PER_HOUR,
            window_seconds=HOUR,
        ):
            raise AuthRateLimitedException()

        code = new_code()
        key = _code_key(purpose, email)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            pipe.hset(key, mapping={"hash": _hash_code(purpose, email, code), "wrong": 0})
            pipe.expire(key, CODE_TTL_SECONDS)
            await pipe.execute()
        return code

    async def verify(self, purpose: OtpPurpose, email: str, code: str) -> None:
        """Accept the code once. Raises CodeInvalid if it is wrong, used or expired.

        After MAX_WRONG_ATTEMPTS wrong tries the code is deleted and a new one is needed.
        """
        email = normalize_email(email)
        key = _code_key(purpose, email)
        stored = await self.redis.hget(key, "hash")
        if stored is None:
            raise CodeInvalidException()

        if not hmac.compare_digest(stored, _hash_code(purpose, email, code.strip())):
            if await self.redis.hincrby(key, "wrong", 1) >= MAX_WRONG_ATTEMPTS:
                await self.redis.delete(key)
            raise CodeInvalidException()

        # Only the request that actually deletes the key wins, so a code works exactly once.
        if not await self.redis.delete(key):
            raise CodeInvalidException()

    async def issue_verification_token(self, email: str) -> str:
        """Proof that `email` was verified, carried by the application form's submit call."""
        token = new_token()
        await self.redis.set(
            f"emv:{hash_token(token)}", normalize_email(email), ex=VERIFICATION_TOKEN_TTL_SECONDS
        )
        return token

    async def peek_verification_token(self, token: str) -> str | None:
        """The verified email for `token`, without using it up (check first, consume on success)."""
        return await self.redis.get(f"emv:{hash_token(token)}")

    async def consume_verification_token(self, token: str) -> str | None:
        """The verified email for `token`, or None. The token cannot be used again."""
        return await self.redis.getdel(f"emv:{hash_token(token)}")
