"""The interactive API docs (/docs): search, login help and the login locks; never in production."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import api_docs
from app.core.config import settings
from app.main import app, create_app

OPENAPI = f"{settings.API_V1_PREFIX}/openapi.json"


@pytest.fixture
async def root() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
async def schema(root: AsyncClient) -> dict:
    response = await root.get(OPENAPI)
    assert response.status_code == 200
    return response.json()


def security_of(schema: dict, method: str, path: str) -> list[str]:
    operation = schema["paths"][f"{settings.API_V1_PREFIX}{path}"][method]
    return [name for entry in operation.get("security", []) for name in entry]


async def test_docs_page_has_the_search_that_matches_paths(root: AsyncClient) -> None:
    response = await root.get("/docs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    page = response.text
    assert f"swagger-ui-dist@{api_docs.SWAGGER_UI_VERSION}/swagger-ui-bundle.js" in page
    assert "opsFilter" in page and 'op.get("path")' in page  # not only tag names
    assert '"filter": true' in page and OPENAPI in page
    assert "noindex" in page


async def test_docs_explain_how_to_log_in(schema: dict) -> None:
    description = schema["info"]["description"]

    assert "How to log in here" in description
    for path in ("/admin/auth/login", "/venue/auth/login", "/auth/customer/consume"):
        assert f"{settings.API_V1_PREFIX}{path}" in description
        assert f"{settings.API_V1_PREFIX}{path}" in schema["paths"]  # the help stays true


async def test_each_login_cookie_is_described(schema: dict) -> None:
    schemes = schema["components"]["securitySchemes"]

    assert {name: (s["type"], s["in"], s["name"]) for name, s in schemes.items()} == {
        "Admin login": ("apiKey", "cookie", "nx_admin_at"),
        "Venue login": ("apiKey", "cookie", "nx_venue_at"),
        "Customer login": ("apiKey", "cookie", "nx_user_at"),
        "Counter phone": ("apiKey", "cookie", "nx_device"),
    }


async def test_endpoints_say_which_login_they_need(schema: dict) -> None:
    assert security_of(schema, "get", "/admin/me") == ["Admin login"]
    assert security_of(schema, "get", "/venue/me") == ["Venue login"]
    assert security_of(schema, "get", "/me") == ["Customer login"]
    assert security_of(schema, "get", "/display/config") == ["Counter phone"]
    # Logging in and public pages need nothing.
    assert security_of(schema, "post", "/admin/auth/login") == []
    assert security_of(schema, "get", "/health") == []


async def test_a_missing_login_is_still_our_own_401(root: AsyncClient) -> None:
    response = await root.get(f"{settings.API_V1_PREFIX}/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.not_authenticated"


async def test_no_docs_in_production(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    production = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=production), base_url="http://test"
    ) as client:
        for path in ("/docs", "/redoc", OPENAPI):
            assert (await client.get(path)).status_code == 404, path
