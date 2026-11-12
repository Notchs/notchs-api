from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=f"http://test{settings.API_V1_PREFIX}"
    ) as ac:
        yield ac


@pytest.fixture(autouse=True)
def _no_global_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test comes from one address; the global limit has its own tests."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "GLOBAL_RATE_LIMIT_PER_MINUTE", 0)
