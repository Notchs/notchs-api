from unittest.mock import AsyncMock

from app.modules.health.service import HealthService


def _service(*, db_ok: bool = True, redis_ok: bool = True) -> HealthService:
    session = AsyncMock()
    redis = AsyncMock()
    if not db_ok:
        session.execute.side_effect = ConnectionError("db down")
    if not redis_ok:
        redis.ping.side_effect = ConnectionError("redis down")
    return HealthService(session, redis)


async def test_ready_when_database_and_redis_respond() -> None:
    result = await _service().readiness()

    assert (result.status, result.database, result.redis) == ("ok", "ok", "ok")


async def test_not_ready_when_redis_is_down() -> None:
    result = await _service(redis_ok=False).readiness()

    assert (result.status, result.database, result.redis) == ("error", "ok", "error")


async def test_not_ready_when_database_is_down() -> None:
    result = await _service(db_ok=False).readiness()

    assert (result.status, result.database, result.redis) == ("error", "error", "ok")
