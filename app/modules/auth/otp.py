"""Emailed 6-digit codes that prove someone owns an address.

Nothing is stored in Postgres. Redis keys:

    otp:{purpose}:{email}        hash: code hash + wrong attempts, 10 min
    otp:cd:{purpose}:{email}     resend cooldown, 60 s
    rl:otp:email:{email}         codes sent to this address in the last hour
    rl:otp:ip:{ip}               codes requested from this IP in the last hour
    emv:{token hash}             verified email, for the application form, 30 min, single use

A code only counts against the cooldown and the hourly limits once the email provider accepted
it: `deliver` gives the attempt back when sending fails, so an email outage (or a wrong sender
setting) never locks people out.
"""

import hashlib
import hmac
import secrets

from redis.asyncio import Redis

from app.clients.email import EmailClient, EmailMessage, EmailSendError
from app.core import rate_limit
from app.core.logging import get_logger
from app.modules.auth.emails import normalize_email
from app.modules.auth.enums import OtpPurpose
from app.modules.auth.exceptions import AuthRateLimitedException, CodeInvalidException
from app.modules.auth.tokens import hash_token, new_token

logger = get_logger(__name__)

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


def _cooldown_key(purpose: OtpPurpose, email: str) -> str:
    return f"otp:cd:{purpose.value}:{email}"


def _email_limit_key(email: str) -> str:
    return f"rl:otp:email:{email}"


def _ip_limit_key(ip: str) -> str:
    return f"rl:otp:ip:{ip}"


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
        if not await self.redis.set(
            _cooldown_key(purpose, email), "1", ex=RESEND_COOLDOWN_SECONDS, nx=True
        ):
            raise AuthRateLimitedException("Please wait a minute before asking for a new code.")
        if not await rate_limit.hit(
            self.redis, _ip_limit_key(ip), limit=MAX_CODES_PER_IP_PER_HOUR, window_seconds=HOUR
        ):
            await self.redis.delete(_cooldown_key(purpose, email))
            raise AuthRateLimitedException(
                "Too many codes from this connection. Please try again in an hour."
            )
        if not await rate_limit.hit(
            self.redis,
            _email_limit_key(email),
            limit=MAX_CODES_PER_EMAIL_PER_HOUR,
            window_seconds=HOUR,
        ):
            await self.redis.delete(_cooldown_key(purpose, email))
            await rate_limit.undo_hit(self.redis, _ip_limit_key(ip))
            raise AuthRateLimitedException(
                "Too many codes for this email. Please try again in an hour."
            )

        code = new_code()
        key = _code_key(purpose, email)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            pipe.hset(key, mapping={"hash": _hash_code(purpose, email, code), "wrong": 0})
            pipe.expire(key, CODE_TTL_SECONDS)
            await pipe.execute()
        return code

    async def release(self, purpose: OtpPurpose, email: str, *, ip: str) -> None:
        """Undo `issue` after the email could not be sent: the code is dropped and the attempt
        does not count against the cooldown or the hourly limits."""
        email = normalize_email(email)
        await self.redis.delete(_code_key(purpose, email), _cooldown_key(purpose, email))
        await rate_limit.undo_hit(self.redis, _ip_limit_key(ip))
        await rate_limit.undo_hit(self.redis, _email_limit_key(email))

    async def deliver(
        self,
        client: EmailClient,
        message: EmailMessage,
        purpose: OtpPurpose,
        *,
        ip: str,
    ) -> None:
        """Send a code email made by `issue`. If the provider refuses it or cannot be reached,
        the attempt is given back (`release`) and EmailSendError is raised."""
        try:
            await client.send(message)
        except EmailSendError as exc:
            await self.release(purpose, message.to, ip=ip)
            # No address or code in the log: the error names the provider's problem.
            logger.error("Could not send a %s code email: %s", purpose.value, exc)
            raise

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
