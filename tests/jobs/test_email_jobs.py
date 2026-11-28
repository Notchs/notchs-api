"""Emails go through the queue: queued by the API, sent by the worker with an idempotency key."""

from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis, FakeServer

from app.clients.email import EmailClient, EmailMessage, EmailSendError
from app.core.config import settings
from app.jobs import email as email_jobs
from app.jobs.email import SEND_EMAIL, EmailQueue, send_email_job
from app.jobs.handlers import HANDLERS
from app.jobs.queue import STREAM
from app.jobs.worker import PermanentJobError, Worker

MESSAGE = EmailMessage(to="owner@kahwa.in", subject="Your code", text="Your code is 123456")


class SentEmails:
    """Replaces EmailClient.send: keeps each email and the idempotency key it came with."""

    def __init__(self, fails: bool = False) -> None:
        self.sent: list[tuple[EmailMessage, str | None]] = []
        self.fails = fails

    # Set on the class, an object (unlike a function) is not bound: no `self` of EmailClient.
    async def __call__(self, message: EmailMessage, *, idempotency_key: str | None = None) -> None:
        if self.fails:
            raise EmailSendError("Resend returned 500")
        self.sent.append((message, idempotency_key))


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> SentEmails:
    recorder = SentEmails()
    monkeypatch.setattr(EmailClient, "send", recorder)
    return recorder


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


def down_redis() -> FakeAsyncRedis:
    server = FakeServer()
    server.connected = False
    return FakeAsyncRedis(server=server, decode_responses=True)


def test_the_worker_knows_the_email_job() -> None:
    assert HANDLERS[SEND_EMAIL] is send_email_job


async def test_queuing_sends_nothing_yet(redis: FakeAsyncRedis, sent: SentEmails) -> None:
    await EmailQueue(redis).enqueue(MESSAGE)

    assert sent.sent == []
    assert await redis.xlen(STREAM) == 1


async def test_the_worker_sends_it_once_with_the_job_id_as_key(
    redis: FakeAsyncRedis, sent: SentEmails
) -> None:
    await EmailQueue(redis).enqueue(MESSAGE)
    ((job_id, _),) = await redis.xrange(STREAM)
    worker = Worker(redis, HANDLERS, consumer="w1")
    await worker.setup()

    await worker.run_once(block_ms=None)
    await worker.run_once(block_ms=None)

    assert sent.sent == [(MESSAGE, f"{settings.ENVIRONMENT}-job-{job_id}")]
    assert await redis.xlen(STREAM) == 0


async def test_a_failed_send_stays_queued_for_a_retry(
    redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(EmailClient, "send", SentEmails(fails=True))
    await EmailQueue(redis).enqueue(MESSAGE)
    worker = Worker(redis, HANDLERS, consumer="w1")
    await worker.setup()

    await worker.run_once(block_ms=None)

    assert (await redis.xpending(STREAM, "workers"))["pending"] == 1


async def test_when_redis_is_down_the_email_is_sent_at_once(sent: SentEmails) -> None:
    await EmailQueue(down_redis()).enqueue(MESSAGE)

    assert sent.sent == [(MESSAGE, None)]


async def test_queuing_never_raises_even_if_sending_fails_too(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(EmailClient, "send", SentEmails(fails=True))

    await EmailQueue(down_redis()).enqueue(MESSAGE)  # no exception

    assert "neither be queued nor sent" in caplog.text
    assert MESSAGE.to not in caplog.text


async def test_a_payload_that_is_not_an_email_is_refused_for_good() -> None:
    with pytest.raises(PermanentJobError):
        await send_email_job({"nope": 1}, "1-0")


def test_the_dependency_uses_the_app_redis() -> None:
    assert email_jobs.get_email_queue().redis is email_jobs.redis_client
