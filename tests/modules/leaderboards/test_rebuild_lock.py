"""Only one API server rebuilds the leaderboards each round (an in-memory Redis, no database)."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fakeredis import FakeAsyncRedis

from app.modules.leaderboards.service import REBUILD_LOCK_KEY, LeaderboardService, rebuild_round


class Rebuilds:
    """Replaces LeaderboardService.rebuild: counts rebuilds, can fail."""

    def __init__(self) -> None:
        self.count = 0
        self.fails = False

    async def __call__(self, session: object) -> int:
        await asyncio.sleep(0)  # let another server's round run meanwhile
        if self.fails:
            raise RuntimeError("the database is down")
        self.count += 1
        return 1


@pytest.fixture
def rebuilds(monkeypatch: pytest.MonkeyPatch) -> Rebuilds:
    recorder = Rebuilds()

    async def rebuild(service: LeaderboardService, session: object) -> int:
        return await recorder(session)

    monkeypatch.setattr(LeaderboardService, "rebuild", rebuild)
    return recorder


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


@asynccontextmanager
async def _session() -> AsyncIterator[object]:
    yield object()


def sessions() -> object:
    return _session()


async def test_two_servers_in_the_same_round_rebuild_once(
    redis: FakeAsyncRedis, rebuilds: Rebuilds
) -> None:
    results = await asyncio.gather(
        rebuild_round(sessions, redis, 3600), rebuild_round(sessions, redis, 3600)
    )

    assert sorted(results) == [False, True]
    assert rebuilds.count == 1


async def test_the_lock_lasts_just_under_a_round(redis: FakeAsyncRedis, rebuilds: Rebuilds) -> None:
    await rebuild_round(sessions, redis, 3600)

    assert 3_000_000 < await redis.pttl(REBUILD_LOCK_KEY) <= 3_240_000  # 90% of an hour


async def test_the_next_round_rebuilds_again_once_the_lock_is_gone(
    redis: FakeAsyncRedis, rebuilds: Rebuilds
) -> None:
    await rebuild_round(sessions, redis, 3600)
    await redis.delete(REBUILD_LOCK_KEY)  # as when it expires

    assert await rebuild_round(sessions, redis, 3600) is True
    assert rebuilds.count == 2


async def test_a_failed_rebuild_lets_the_next_try_go_ahead(
    redis: FakeAsyncRedis, rebuilds: Rebuilds
) -> None:
    rebuilds.fails = True
    with pytest.raises(RuntimeError):
        await rebuild_round(sessions, redis, 3600)

    rebuilds.fails = False
    assert await rebuild_round(sessions, redis, 3600) is True


async def test_a_failure_never_removes_another_servers_lock(
    redis: FakeAsyncRedis, rebuilds: Rebuilds, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fails_after_losing_the_lock(service: LeaderboardService, session: object) -> int:
        await redis.set(REBUILD_LOCK_KEY, "another-server")  # ours expired; someone took it
        raise RuntimeError("the database is down")

    monkeypatch.setattr(LeaderboardService, "rebuild", fails_after_losing_the_lock)

    with pytest.raises(RuntimeError):
        await rebuild_round(sessions, redis, 3600)

    assert await redis.get(REBUILD_LOCK_KEY) == "another-server"
