"""Async Redis client: rate limits, one-time codes, leaderboards and the job queue.

Redis is local to each developer (README, "Redis"), unlike the shared Postgres.
"""

from redis.asyncio import Redis

from app.core.config import settings


def create_redis() -> Redis:
    return Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=settings.REDIS_TIMEOUT_SECONDS,
        socket_timeout=settings.REDIS_TIMEOUT_SECONDS,
        health_check_interval=30,
    )


# Connects lazily on first command, so importing this module never needs a running Redis.
redis_client = create_redis()


async def check_redis(client: Redis | None = None) -> dict[str, str]:
    """Ping Redis and report where it landed. Raises if Redis is unreachable."""
    client = client or redis_client
    await client.ping()
    info = await client.info("server")
    kwargs = client.connection_pool.connection_kwargs
    return {
        "host": f"{kwargs.get('host', 'localhost')}:{kwargs.get('port', 6379)}",
        "db": str(kwargs.get("db", 0)),
        "version": str(info.get("redis_version", "unknown")),
    }


async def get_redis() -> Redis:
    """FastAPI dependency. Override it in tests to swap the client."""
    return redis_client
