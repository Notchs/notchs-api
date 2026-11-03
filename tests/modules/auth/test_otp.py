from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis

from app.clients.email import EmailMessage, EmailSendError
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


# --- a code only counts once the email provider accepted it --------------------------------------


class FakeEmailClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        if self.fail:
            raise EmailSendError("Resend returned 403: domain not verified")
        self.sent.append(message)


def _message(email: str = EMAIL) -> EmailMessage:
    return EmailMessage(to=email, subject="Your Notchs code: 123456", text="123456")


async def _send(codes: EmailCodes, client: FakeEmailClient, email: str = EMAIL) -> None:
    await codes.issue(APP, email, ip=IP)
    await codes.deliver(client, _message(email), APP, ip=IP)  # type: ignore[arg-type]


async def test_a_delivered_code_counts(codes: EmailCodes, redis: FakeAsyncRedis) -> None:
    client = FakeEmailClient()

    await _send(codes, client)

    assert len(client.sent) == 1
    assert await redis.get(f"rl:otp:email:{EMAIL}") == "1"
    assert await redis.get(f"rl:otp:ip:{IP}") == "1"
    with pytest.raises(AuthRateLimitedException):  # the 1-minute cooldown applies
        await codes.issue(APP, EMAIL, ip=IP)


async def test_a_failed_send_is_given_back(codes: EmailCodes, redis: FakeAsyncRedis) -> None:
    with pytest.raises(EmailSendError):
        await _send(codes, FakeEmailClient(fail=True))

    assert await redis.exists(f"otp:{APP.value}:{EMAIL}") == 0  # the code is gone
    assert await redis.exists(f"otp:cd:{APP.value}:{EMAIL}") == 0  # no cooldown
    assert await redis.exists(f"rl:otp:email:{EMAIL}") == 0
    assert await redis.exists(f"rl:otp:ip:{IP}") == 0
    await _send(codes, FakeEmailClient())  # works straight away


async def test_an_email_outage_never_locks_anyone_out(codes: EmailCodes) -> None:
    for _ in range(MAX_CODES_PER_IP_PER_HOUR + 5):
        with pytest.raises(EmailSendError):
            await _send(codes, FakeEmailClient(fail=True))

    await _send(codes, FakeEmailClient())  # still allowed once email works again


async def test_a_failed_send_keeps_earlier_successful_counts(
    codes: EmailCodes, redis: FakeAsyncRedis
) -> None:
    await _send(codes, FakeEmailClient())
    await _skip_cooldown(redis)

    with pytest.raises(EmailSendError):
        await _send(codes, FakeEmailClient(fail=True))

    assert await redis.get(f"rl:otp:email:{EMAIL}") == "1"
    assert await redis.ttl(f"rl:otp:email:{EMAIL}") > 0  # the window keeps its end


async def test_limit_messages_say_how_long_to_wait(codes: EmailCodes, redis) -> None:
    for i in range(MAX_CODES_PER_EMAIL_PER_HOUR):
        await codes.issue(APP, EMAIL, ip=f"10.0.0.{i}")
        await _skip_cooldown(redis)
    with pytest.raises(AuthRateLimitedException, match=r"this email.*an hour"):
        await codes.issue(APP, EMAIL, ip="10.0.0.99")

    for i in range(MAX_CODES_PER_IP_PER_HOUR):
        await codes.issue(APP, f"other{i}@kahwa.in", ip=IP)
    with pytest.raises(AuthRateLimitedException, match=r"this connection.*an hour"):
        await codes.issue(APP, "one-more@kahwa.in", ip=IP)


async def test_a_refused_request_does_not_start_a_cooldown(codes: EmailCodes, redis) -> None:
    for i in range(MAX_CODES_PER_IP_PER_HOUR):
        await codes.issue(APP, f"other{i}@kahwa.in", ip=IP)

    with pytest.raises(AuthRateLimitedException):
        await codes.issue(APP, EMAIL, ip=IP)

    assert await redis.exists(f"otp:cd:{APP.value}:{EMAIL}") == 0
    assert await redis.exists(f"rl:otp:email:{EMAIL}") == 0  # not counted for this email
    await codes.issue(APP, EMAIL, ip="198.51.100.1")  # another connection may ask at once


async def test_undo_hit(redis: FakeAsyncRedis) -> None:
    await rate_limit.hit(redis, "rl:test", limit=5, window_seconds=100)
    await rate_limit.hit(redis, "rl:test", limit=5, window_seconds=100)

    await rate_limit.undo_hit(redis, "rl:test")
    assert await redis.get("rl:test") == "1"
    assert 0 < await redis.ttl("rl:test") <= 100

    await rate_limit.undo_hit(redis, "rl:test")
    await rate_limit.undo_hit(redis, "rl:missing")  # expired meanwhile: nothing left behind
    assert await redis.exists("rl:test") == 0
    assert await redis.exists("rl:missing") == 0
