"""The job worker: reads the `jobs` stream as one consumer of the `workers` group.

each job once     the consumer group hands every new job to one worker only, however many
                  run (inside each API server and/or `python -m app.jobs.worker`)
success           a done marker is kept for a day, then the entry is acknowledged and deleted
failure           the entry stays pending and is tried again after 1, 2, 4, 8 and 16 minutes;
                  after MAX_ATTEMPTS it is dropped with an error in the log
crash             a job a stopped worker had taken stays pending, so another worker claims it
                  on the same schedule
never twice       a job whose done marker exists is only cleaned up, never run again (and the
                  email job also gives Resend the job id as its Idempotency-Key)
Redis down        the loop logs it and tries again every few seconds; it never stops
"""

import asyncio
import contextlib
import json
import os
import signal
import socket
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from app.core.logging import get_logger
from app.jobs.queue import GROUP, STREAM

logger = get_logger(__name__)

# A job handler gets the payload and the job id (to use as an idempotency key).
Handler = Callable[[dict[str, Any], str], Awaitable[None]]

BATCH = 10
BLOCK_MS = 2000  # under the Redis client's socket timeout (REDIS_TIMEOUT_SECONDS, 5 s)
RETRY_BASE_MS = 60_000  # the first retry waits a minute; each later one waits twice as long
MAX_ATTEMPTS = 6  # the first try and 5 retries: about half an hour in all
DONE_PREFIX = "jobs:done:"
DONE_TTL_SECONDS = 86_400
REDIS_DOWN_PAUSE_SECONDS = 5.0


class PermanentJobError(Exception):
    """The job can never succeed (unknown kind, bad payload): dropped without retries."""


def retry_delay_ms(attempts: int) -> int:
    """How long a job waits after its `attempts`-th try failed: 1, 2, 4, 8, 16 minutes."""
    return RETRY_BASE_MS * 2 ** max(0, attempts - 1)


def consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


class Worker:
    def __init__(
        self, redis: Redis, handlers: Mapping[str, Handler], consumer: str | None = None
    ) -> None:
        self.redis = redis
        self.handlers = handlers
        self.consumer = consumer or consumer_name()

    async def setup(self) -> None:
        """Create the stream and the group (from the start, so jobs queued before any worker
        ran are not missed). Safe to repeat."""
        try:
            await self.redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def run_once(self, block_ms: int | None = BLOCK_MS) -> int:
        """Retry the jobs that are due, then run new ones (waiting up to `block_ms` for them;
        None: don't wait). Returns how many jobs were looked at."""
        handled = 0
        for job_id, fields in await self._due_retries():
            await self._run(job_id, fields)
            handled += 1
        response = await self.redis.xreadgroup(
            GROUP, self.consumer, {STREAM: ">"}, count=BATCH, block=block_ms or None
        )
        for _, entries in response or []:
            for job_id, fields in entries:
                await self._run(job_id, fields)
                handled += 1
        return handled

    async def run_forever(self) -> None:
        ready = False
        while True:
            try:
                if not ready:
                    await self.setup()
                    ready = True
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except RedisError:
                # Also covers NOGROUP after the stream was wiped: set it up again.
                ready = False
                logger.warning("Job worker: Redis did not answer; trying again shortly")
                await asyncio.sleep(REDIS_DOWN_PAUSE_SECONDS)
            except Exception:
                logger.exception("Job worker: unexpected error; trying again shortly")
                await asyncio.sleep(REDIS_DOWN_PAUSE_SECONDS)

    async def _due_retries(self) -> list[tuple[str, dict[str, str]]]:
        pending = await self.redis.xpending_range(
            STREAM, GROUP, min="-", max="+", count=BATCH, idle=RETRY_BASE_MS
        )
        due: list[str] = []
        for entry in pending:
            job_id, attempts = entry["message_id"], entry["times_delivered"]
            if attempts >= MAX_ATTEMPTS:
                logger.error("Job %s dropped after %d failed attempts", job_id, attempts)
                await self._finish(job_id)
            elif entry["time_since_delivered"] >= retry_delay_ms(attempts):
                due.append(job_id)
        if not due:
            return []
        # min_idle_time: if another worker claimed one a moment ago, it is not taken again.
        claimed = await self.redis.xclaim(
            STREAM, GROUP, self.consumer, min_idle_time=RETRY_BASE_MS, message_ids=due
        )
        return [(job_id, fields) for job_id, fields in claimed if fields]

    async def _run(self, job_id: str, fields: dict[str, str]) -> None:
        done_key = f"{DONE_PREFIX}{job_id}"
        if await self.redis.exists(done_key):
            await self._finish(job_id)  # done before; only the clean-up was missed
            return
        kind = fields.get("kind", "")
        try:
            handler = self.handlers.get(kind)
            if handler is None:
                raise PermanentJobError(f"no handler for kind {kind!r}")
            try:
                payload = json.loads(fields.get("payload") or "{}")
            except json.JSONDecodeError as exc:
                raise PermanentJobError("payload is not JSON") from exc
            await handler(payload, job_id)
        except asyncio.CancelledError:
            raise
        except PermanentJobError as exc:
            logger.error("Job %s (%s) dropped: %s", job_id, kind, exc)
            await self._finish(job_id)
            return
        except Exception as exc:
            # Never the payload: an email's address and text stay out of the log.
            logger.warning("Job %s (%s) failed and will be retried: %s", job_id, kind, exc)
            return
        await self.redis.set(done_key, "1", ex=DONE_TTL_SECONDS)
        await self._finish(job_id)
        logger.info("Job %s (%s) done", job_id, kind)

    async def _finish(self, job_id: str) -> None:
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.xack(STREAM, GROUP, job_id)
            pipe.xdel(STREAM, job_id)
            await pipe.execute()


async def main() -> None:
    """`python -m app.jobs.worker`: a worker on its own, until SIGTERM or Ctrl+C."""
    from app.core.logging import setup_logging
    from app.core.redis import check_redis, redis_client
    from app.jobs.handlers import HANDLERS

    setup_logging()
    cache = await check_redis()
    worker = Worker(redis_client, HANDLERS)
    logger.info("Job worker %s started (Redis %s)", worker.consumer, cache["host"])
    task = asyncio.create_task(worker.run_forever(), name="job-worker")
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # Windows has no signal handlers
            loop.add_signal_handler(sig, task.cancel)
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await redis_client.aclose()
    logger.info("Job worker %s stopped", worker.consumer)


if __name__ == "__main__":
    asyncio.run(main())
