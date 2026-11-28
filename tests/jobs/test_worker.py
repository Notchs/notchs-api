"""The job worker on an in-memory Redis: runs each job once, retries, recovers, never twice."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis

from app.jobs import worker as worker_module
from app.jobs.queue import GROUP, STREAM, enqueue
from app.jobs.worker import DONE_PREFIX, MAX_ATTEMPTS, PermanentJobError, Worker, retry_delay_ms


class Recorder:
    """A job handler that keeps what it ran, and fails the first `failures` times."""

    def __init__(self, failures: int = 0) -> None:
        self.calls: list[tuple[dict[str, Any], str]] = []
        self.failures = failures

    async def __call__(self, payload: dict[str, Any], job_id: str) -> None:
        self.calls.append((payload, job_id))
        if len(self.calls) <= self.failures:
            raise RuntimeError("provider down")


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def instant_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries due after 1 ms instead of minutes."""
    monkeypatch.setattr(worker_module, "RETRY_BASE_MS", 1)


async def worker(redis: FakeAsyncRedis, handler: Recorder, name: str = "w1") -> Worker:
    w = Worker(redis, {"test": handler}, consumer=name)
    await w.setup()
    return w


async def pending(redis: FakeAsyncRedis) -> int:
    return (await redis.xpending(STREAM, GROUP))["pending"]


def test_retries_wait_1_2_4_8_16_minutes() -> None:
    assert [retry_delay_ms(n) // 60_000 for n in range(1, 6)] == [1, 2, 4, 8, 16]


async def test_a_job_runs_once_and_is_deleted(redis: FakeAsyncRedis) -> None:
    handler = Recorder()
    w = await worker(redis, handler)
    job_id = await enqueue(redis, "test", {"n": 1})

    assert await w.run_once(block_ms=None) == 1
    assert await w.run_once(block_ms=None) == 0

    assert handler.calls == [({"n": 1}, job_id)]
    assert await redis.xlen(STREAM) == 0  # nothing (e.g. an email's code) left behind
    assert await pending(redis) == 0
    assert await redis.exists(f"{DONE_PREFIX}{job_id}")


async def test_jobs_queued_before_any_worker_ran_are_not_lost(redis: FakeAsyncRedis) -> None:
    await enqueue(redis, "test", {"n": 1})
    handler = Recorder()

    await (await worker(redis, handler)).run_once(block_ms=None)

    assert len(handler.calls) == 1


async def test_two_workers_never_run_the_same_job(redis: FakeAsyncRedis) -> None:
    handler = Recorder()
    first, second = await worker(redis, handler, "w1"), await worker(redis, handler, "w2")
    for n in range(5):
        await enqueue(redis, "test", {"n": n})

    await asyncio.gather(first.run_once(block_ms=None), second.run_once(block_ms=None))

    assert sorted(payload["n"] for payload, _ in handler.calls) == [0, 1, 2, 3, 4]


async def test_a_failed_job_is_retried_until_it_works(
    redis: FakeAsyncRedis, instant_retries: None
) -> None:
    handler = Recorder(failures=2)
    w = await worker(redis, handler)
    job_id = await enqueue(redis, "test", {"n": 1})

    await w.run_once(block_ms=None)
    assert await pending(redis) == 1  # kept for a retry
    for _ in range(2):
        await asyncio.sleep(0.01)
        await w.run_once(block_ms=None)

    assert [called for _, called in handler.calls] == [job_id] * 3
    assert await pending(redis) == 0
    assert await redis.xlen(STREAM) == 0


async def test_a_retry_waits_for_its_turn(redis: FakeAsyncRedis) -> None:
    handler = Recorder(failures=1)
    w = await worker(redis, handler)
    await enqueue(redis, "test", {"n": 1})

    await w.run_once(block_ms=None)
    await w.run_once(block_ms=None)  # straight away: the minute has not passed

    assert len(handler.calls) == 1
    assert await pending(redis) == 1


async def test_a_job_that_keeps_failing_is_dropped(
    redis: FakeAsyncRedis, instant_retries: None, caplog: pytest.LogCaptureFixture
) -> None:
    handler = Recorder(failures=100)
    w = await worker(redis, handler)
    job_id = await enqueue(redis, "test", {"n": 1})

    for _ in range(MAX_ATTEMPTS + 2):
        await w.run_once(block_ms=None)
        await asyncio.sleep(0.05)  # longer than the last retry delay (32 ms at 1 ms a step)

    assert len(handler.calls) == MAX_ATTEMPTS
    assert await pending(redis) == 0
    assert await redis.xlen(STREAM) == 0
    assert f"Job {job_id} dropped after {MAX_ATTEMPTS} failed attempts" in caplog.text


async def test_a_stopped_workers_job_is_taken_over(
    redis: FakeAsyncRedis, instant_retries: None
) -> None:
    await worker(redis, Recorder())
    job_id = await enqueue(redis, "test", {"n": 1})
    # A worker took the job, then stopped before finishing it.
    await redis.xreadgroup(GROUP, "crashed", {STREAM: ">"}, count=1)
    handler = Recorder()
    await asyncio.sleep(0.01)

    await (await worker(redis, handler, "w2")).run_once(block_ms=None)

    assert handler.calls == [({"n": 1}, job_id)]
    assert await pending(redis) == 0


async def test_a_job_already_done_is_never_run_again(redis: FakeAsyncRedis) -> None:
    handler = Recorder()
    w = await worker(redis, handler)
    job_id = await enqueue(redis, "test", {"n": 1})
    # Done before, but the worker stopped before it could clean up.
    await redis.set(f"{DONE_PREFIX}{job_id}", "1")

    await w.run_once(block_ms=None)

    assert handler.calls == []
    assert await redis.xlen(STREAM) == 0


@pytest.mark.parametrize(
    "fields",
    [{"kind": "nobody-handles-this", "payload": "{}"}, {"kind": "test", "payload": "not json"}],
)
async def test_jobs_that_can_never_work_are_dropped_at_once(
    redis: FakeAsyncRedis, fields: dict[str, str]
) -> None:
    handler = Recorder()
    w = await worker(redis, handler)
    await redis.xadd(STREAM, fields)

    await w.run_once(block_ms=None)

    assert handler.calls == []
    assert await pending(redis) == 0


async def test_a_handler_can_refuse_a_job_for_good(redis: FakeAsyncRedis) -> None:
    async def refuses(payload: dict[str, Any], job_id: str) -> None:
        raise PermanentJobError("bad payload")

    w = Worker(redis, {"test": refuses}, consumer="w1")
    await w.setup()
    await enqueue(redis, "test", {"n": 1})

    await w.run_once(block_ms=None)

    assert await pending(redis) == 0


async def test_setup_can_be_repeated(redis: FakeAsyncRedis) -> None:
    w = await worker(redis, Recorder())
    await w.setup()  # BUSYGROUP is fine


async def test_the_payload_never_reaches_the_log(
    redis: FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    w = await worker(redis, Recorder(failures=1))
    await enqueue(redis, "test", {"to": "owner@kahwa.in", "text": "Your code is 123456"})

    await w.run_once(block_ms=None)

    assert "owner@kahwa.in" not in caplog.text
    assert "123456" not in caplog.text


async def test_the_stored_job_is_json(redis: FakeAsyncRedis) -> None:
    await enqueue(redis, "test", {"n": 1})
    ((_, fields),) = await redis.xrange(STREAM)
    assert fields == {"kind": "test", "payload": json.dumps({"n": 1})}
