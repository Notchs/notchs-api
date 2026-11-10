from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis

from app.core import rate_limit
from app.modules.auth.enums import OtpPurpose
from app.modules.auth.exceptions import AuthRateLimitedException, CodeInvalidException
from app.modules.auth.otp import (
    CODE_TTL_SECONDS,
    MAX_CODES_PER_EMAIL_PER_HOUR,
    MAX_CODES_PER_IP_PER_HOUR,
    MAX_WRONG_ATTEMPTS,
    EmailCodes,
    new_code,
)

APP = OtpPurpose.APPLICATION
EMAIL = "owner@kahwa.in"
IP = "203.0.113.7"


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def codes(redis: FakeAsyncRedis) -> EmailCodes:
    return EmailCodes(redis)


async def _skip_cooldown(redis: FakeAsyncRedis, email: str = EMAIL) -> None:
    await redis.delete(f"otp:cd:{APP.value}:{email}")


def test_codes_are_six_digits() -> None:
    assert all(len(c) == 6 and c.isdigit() for c in (new_code() for _ in range(500)))


async def test_right_code_works_once(codes: EmailCodes) -> None:
    code = await codes.issue(APP, EMAIL, ip=IP)

    await codes.verify(APP, EMAIL, code)
    with pytest.raises(CodeInvalidException):
        await codes.verify(APP, EMAIL, code)


async def test_email_case_and_spaces_do_not_matter(codes: EmailCodes) -> None:
    code = await codes.issue(APP, " Owner@Kahwa.IN", ip=IP)

    await codes.verify(APP, "owner@kahwa.in ", f" {code} ")


async def test_code_is_tied_to_purpose_and_email(codes: EmailCodes) -> None:
    code = await codes.issue(APP, EMAIL, ip=IP)

    with pytest.raises(CodeInvalidException):
        await codes.verify(OtpPurpose.EMAIL_CHANGE, EMAIL, code)
    with pytest.raises(CodeInvalidException):
        await codes.verify(APP, "other@kahwa.in", code)


async def test_code_expires(codes: EmailCodes, redis: FakeAsyncRedis) -> None:
    await codes.issue(APP, EMAIL, ip=IP)

    assert 0 < await redis.ttl(f"otp:{APP.value}:{EMAIL}") <= CODE_TTL_SECONDS


async def test_too_many_wrong_attempts_kill_the_code(codes: EmailCodes) -> None:
    code = await codes.issue(APP, EMAIL, ip=IP)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(MAX_WRONG_ATTEMPTS):
        with pytest.raises(CodeInvalidException):
            await codes.verify(APP, EMAIL, wrong)
    with pytest.raises(CodeInvalidException):
        await codes.verify(APP, EMAIL, code)


async def test_new_code_replaces_the_old_one(codes: EmailCodes, redis: FakeAsyncRedis) -> None:
    first = await codes.issue(APP, EMAIL, ip=IP)
    await _skip_cooldown(redis)
    second = await codes.issue(APP, EMAIL, ip=IP)

    if first != second:
        with pytest.raises(CodeInvalidException):
            await codes.verify(APP, EMAIL, first)
    await codes.verify(APP, EMAIL, second)


async def test_resend_cooldown(codes: EmailCodes) -> None:
    await codes.issue(APP, EMAIL, ip=IP)

    with pytest.raises(AuthRateLimitedException):
        await codes.issue(APP, EMAIL, ip=IP)


async def test_hourly_limit_per_email(codes: EmailCodes, redis: FakeAsyncRedis) -> None:
    for i in range(MAX_CODES_PER_EMAIL_PER_HOUR):
        await codes.issue(APP, EMAIL, ip=f"10.0.0.{i}")
        await _skip_cooldown(redis)

    with pytest.raises(AuthRateLimitedException):
        await codes.issue(APP, EMAIL, ip="10.0.0.99")


async def test_hourly_limit_per_ip(codes: EmailCodes) -> None:
    for i in range(MAX_CODES_PER_IP_PER_HOUR):
        await codes.issue(APP, f"owner{i}@kahwa.in", ip=IP)

    with pytest.raises(AuthRateLimitedException):
        await codes.issue(APP, "one-more@kahwa.in", ip=IP)


async def test_verification_token_is_single_use(codes: EmailCodes) -> None:
    token = await codes.issue_verification_token(" Owner@Kahwa.IN ")

    assert await codes.consume_verification_token(token) == EMAIL
    assert await codes.consume_verification_token(token) is None
    assert await codes.consume_verification_token("made-up") is None


async def test_rate_limit_window_is_not_extended_by_later_hits(redis: FakeAsyncRedis) -> None:
    assert await rate_limit.hit(redis, "rl:test", limit=2, window_seconds=100)
    await redis.expire("rl:test", 50)
    assert await rate_limit.hit(redis, "rl:test", limit=2, window_seconds=100)
    assert not await rate_limit.hit(redis, "rl:test", limit=2, window_seconds=100)

    assert await redis.ttl("rl:test") <= 50
    assert await rate_limit.is_limited(redis, "rl:test", limit=2)
    await rate_limit.reset(redis, "rl:test")
    assert not await rate_limit.is_limited(redis, "rl:test", limit=2)
