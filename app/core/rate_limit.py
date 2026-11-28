"""Fixed-window rate limits in Redis.

Each key counts hits in a window that starts with the first hit and lasts `window_seconds`.
Keys are prefixed `rl:`, e.g. `rl:login:<email>:<ip>`.
"""

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger

logger = get_logger(__name__)


async def hit(redis: Redis, key: str, *, limit: int, window_seconds: int) -> bool:
    """Count one hit. Returns False once `limit` hits have already happened in the window."""
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        # NX: only the first hit of a window sets the expiry, so later hits never extend it.
        pipe.expire(key, window_seconds, nx=True)
        count, _ = await pipe.execute()
    return int(count) <= limit


async def hit_or_allow(redis: Redis, key: str, *, limit: int, window_seconds: int) -> bool:
    """`hit`, but allows the request when Redis does not answer. Only for public reads where an
    outage should skip the limit rather than the page (the map); limits that guard accounts or
    codes use `hit`, and a Redis outage there is a 503."""
    try:
        return await hit(redis, key, limit=limit, window_seconds=window_seconds)
    except RedisError:
        logger.warning("Rate limit %s skipped: Redis did not answer", key.split(":", 2)[1])
        return True


async def is_limited(redis: Redis, key: str, *, limit: int) -> bool:
    """True if `limit` hits were already counted, without counting a new one."""
    count = await redis.get(key)
    return count is not None and int(count) >= limit


async def reset(redis: Redis, key: str) -> None:
    await redis.delete(key)
