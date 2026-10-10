from httpx import AsyncClient

from app.core.config import settings


async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == settings.APP_VERSION
    assert "X-Request-ID" in response.headers


async def test_unknown_route_returns_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"
