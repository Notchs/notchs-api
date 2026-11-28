"""When Redis is down: public reads keep working, anything that needs it answers a clean 503."""

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.main import app
from tests.integration.test_venue_map import add_city, add_place

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def redis_down(api: AsyncClient) -> None:
    server = FakeServer()
    server.connected = False
    down = FakeAsyncRedis(server=server, decode_responses=True)
    app.dependency_overrides[get_redis] = lambda: down
    # The api fixture removes its overrides, this one included, when the test ends.


async def test_the_map_still_works_without_its_limit_and_cache(
    api: AsyncClient, db: AsyncSession, redis_down: None
) -> None:
    city = await add_city(db)
    await add_place(db, city, "Kahwa House")
    await db.commit()

    response = await api.get("/venues/map", params={"city": city.slug})

    assert response.status_code == 200
    names = [f["properties"]["name"] for f in response.json()["data"]["venues"]["features"]]
    assert names == ["Kahwa House"]


async def test_what_needs_redis_answers_503_not_500(api: AsyncClient, redis_down: None) -> None:
    response = await api.post(
        "/admin/auth/forgot-password", json={"email": "someone@notchs.com", "turnstile_token": "ok"}
    )

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": {
            "code": "service_unavailable",
            "message": "This is unavailable for a moment. Please try again in a minute.",
            "details": None,
        },
    }
