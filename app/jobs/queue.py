"""Putting jobs on the queue: one Redis stream, `jobs`.

Each job is a stream entry `{kind, payload}` (payload is JSON); its entry id is the job id. The
worker (worker.py) deletes an entry once its job is done or dropped, so the stream stays short
and an email's contents (which can hold a login code) never stay in Redis for long.
"""

import json
from typing import Any

from redis.asyncio import Redis

STREAM = "jobs"
GROUP = "workers"
# A safety cap only: entries are deleted when done, so the stream is normally near empty.
MAX_LENGTH = 10_000


async def enqueue(redis: Redis, kind: str, payload: dict[str, Any]) -> str:
    """Add a job; returns its id. Raises RedisError if Redis does not answer."""
    return await redis.xadd(
        STREAM,
        {"kind": kind, "payload": json.dumps(payload)},
        maxlen=MAX_LENGTH,
        approximate=True,
    )
