from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.core.redis import check_redis, create_redis, get_redis, redis_client


def test_client_is_built_from_settings() -> None:
    client = create_redis()
    kwargs = client.connection_pool.connection_kwargs

    assert kwargs["decode_responses"] is True
    assert kwargs["socket_timeout"] == settings.REDIS_TIMEOUT_SECONDS
    assert kwargs["socket_connect_timeout"] == settings.REDIS_TIMEOUT_SECONDS


async def test_get_redis_returns_the_shared_client() -> None:
    assert await get_redis() is redis_client


async def test_check_redis_reports_where_it_connected() -> None:
    client = MagicMock()
    client.ping = AsyncMock(return_value=True)
    client.info = AsyncMock(return_value={"redis_version": "7.4.1"})
    client.connection_pool.connection_kwargs = {"host": "redis", "port": 6379, "db": 2}

    assert await check_redis(client) == {"host": "redis:6379", "db": "2", "version": "7.4.1"}


async def test_check_redis_raises_when_unreachable() -> None:
    client = MagicMock()
    client.ping = AsyncMock(side_effect=ConnectionError("refused"))

    with pytest.raises(ConnectionError):
        await check_redis(client)


@pytest.mark.parametrize("url", ["redis://localhost:6379/0", "rediss://user:pw@host:6380/1"])
def test_valid_redis_url(url: str) -> None:
    assert url == Settings(REDIS_URL=url).REDIS_URL


@pytest.mark.parametrize("url", ["", "localhost:6379", "http://localhost:6379"])
def test_invalid_redis_url_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(REDIS_URL=url)
