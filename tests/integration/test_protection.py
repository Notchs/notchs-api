"""Every request: the visitor's real address, the global limit, body size, safe headers; and
password guessing stopped per account, whatever address it comes from."""

import pytest
from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from pydantic import SecretStr
from starlette.requests import Request

from app.common.http import ClientAddress, client_address, client_ip
from app.core.config import settings
from app.middleware import protection
from app.modules.auth.password_auth import MAX_LOGIN_FAILURES_PER_ACCOUNT
from tests.integration.test_admin import ADMIN_PASSWORD, make_admin

pytestmark = pytest.mark.asyncio(loop_scope="session")


def request_with(headers: dict[str, str], peer: str = "10.201.5.9") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 1234)})


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        # Through the website: Netlify says who the visitor is.
        ({"X-Nf-Client-Connection-Ip": "49.36.1.2", "CF-Connecting-IP": "3.7.8.9"}, "49.36.1.2"),
        # Straight to the API: the edge says who connected.
        ({"CF-Connecting-IP": "49.36.1.2", "X-Forwarded-For": "1.1.1.1, 49.36.1.2"}, "49.36.1.2"),
        ({"True-Client-IP": "2401:4900::1"}, "2401:4900::1"),
        # Only X-Forwarded-For: the last public entry; the caller's made-up first one is ignored.
        ({"X-Forwarded-For": "8.8.8.8, 49.36.1.2, 10.0.0.3"}, "49.36.1.2"),
        # Nonsense and private values fall through to the socket.
        ({"X-Nf-Client-Connection-Ip": "not-an-ip", "X-Forwarded-For": "10.0.0.1"}, "10.201.5.9"),
        ({}, "10.201.5.9"),
    ],
)
def test_the_visitors_address(headers: dict[str, str], expected: str) -> None:
    assert client_ip(request_with(headers)) == expected


def test_with_a_proxy_secret_only_the_website_is_believed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PROXY_SECRET", SecretStr("a-long-shared-secret-value"))
    website = {
        "X-Notchs-Proxy-Secret": "a-long-shared-secret-value",
        "X-Notchs-Client-Ip": "49.36.1.2",
        "CF-Connecting-IP": "3.7.8.9",  # Netlify's server, as the edge sees it
    }
    forged = {
        "X-Notchs-Proxy-Secret": "a-guess",
        "X-Notchs-Client-Ip": "1.2.3.4",
        "X-Nf-Client-Connection-Ip": "1.2.3.4",
        "CF-Connecting-IP": "49.40.5.5",  # the caller's real address
    }

    assert client_address(request_with(website)) == ClientAddress("49.36.1.2", "website")
    # A direct caller can no longer invent an address: the edge's word is used.
    assert client_address(request_with(forged)) == ClientAddress("49.40.5.5", "cf-connecting-ip")


async def test_whoami_tells_the_caller_their_own_address(api: AsyncClient) -> None:
    response = await api.get("/health/whoami", headers={"X-Nf-Client-Connection-Ip": "49.36.1.2"})

    assert response.status_code == 200
    assert response.json() == {
        "ip": "49.36.1.2",
        "source": "netlify",
        "through_website": False,
        "headers_present": ["x-nf-client-connection-ip"],
        "forwarded_hops": 0,
    }


def test_admin_settings_answer_to_the_short_names(monkeypatch) -> None:
    monkeypatch.delenv("FIRST_ADMIN_EMAIL", raising=False)
    monkeypatch.setenv("ADMIN_EMAIL", "Harsh@Notchs.Test")
    monkeypatch.setenv("ADMIN_NAME", "Harsh")
    monkeypatch.setenv("ADMIN_PASSWORD", "a long first password")

    fresh = type(settings)()

    assert (fresh.FIRST_ADMIN_EMAIL, fresh.FIRST_ADMIN_NAME) == ("harsh@notchs.test", "Harsh")
    assert fresh.FIRST_ADMIN_PASSWORD.get_secret_value() == "a long first password"


@pytest.fixture
def global_limit(monkeypatch):
    """The global limit switched on at 5 a minute, on an in-memory Redis."""
    monkeypatch.setattr(settings, "GLOBAL_RATE_LIMIT_PER_MINUTE", 5)
    monkeypatch.setattr(protection, "redis_client", FakeAsyncRedis(decode_responses=True))


async def test_global_limit_is_per_visitor(api: AsyncClient, global_limit) -> None:
    asha = {"X-Nf-Client-Connection-Ip": "49.36.1.2"}
    ben = {"X-Nf-Client-Connection-Ip": "49.36.7.7"}

    codes = [
        (await api.get("/dropdowns/venue_categories", headers=asha)).status_code for _ in range(7)
    ]
    other = await api.get("/dropdowns/venue_categories", headers=ben)
    limited = await api.get("/dropdowns/venue_categories", headers=asha)

    assert codes == [200] * 5 + [429] * 2
    assert other.status_code == 200  # another visitor is not affected
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.headers["retry-after"] == "60"


async def test_health_and_the_webhook_are_never_counted(api: AsyncClient, global_limit) -> None:
    for _ in range(8):
        assert (await api.get("/health")).status_code == 200
    # Meta's servers: refused for a bad signature (401/503), never for the limit.
    codes = {(await api.post("/webhooks/whatsapp", content=b"{}")).status_code for _ in range(8)}
    assert 429 not in codes


async def test_a_failing_redis_never_blocks_requests(api: AsyncClient, monkeypatch) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    class Down:
        def pipeline(self, *_, **__):
            raise RedisConnectionError("down")

    monkeypatch.setattr(settings, "GLOBAL_RATE_LIMIT_PER_MINUTE", 5)
    monkeypatch.setattr(protection, "redis_client", Down())

    assert (await api.get("/dropdowns/venue_categories")).status_code == 200


async def test_huge_bodies_are_refused_before_reading(api: AsyncClient) -> None:
    response = await api.post(
        "/admin/auth/login",
        content=b"x",
        headers={"Content-Length": str(settings.MAX_REQUEST_BODY_BYTES + 1)},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


async def test_private_answers_are_never_cached(api: AsyncClient, db, redis) -> None:
    admin = await make_admin(db, redis, api)

    private = await api.get("/admin/me", cookies=admin)
    public = await api.get("/dropdowns/venue_categories")

    assert private.headers["cache-control"] == "no-store"
    assert private.headers["x-content-type-options"] == "nosniff"
    assert public.headers["cache-control"] == "public, max-age=300"  # the route's own choice


async def test_password_guessing_stops_per_account_from_any_address(
    api: AsyncClient, db, redis
) -> None:
    cookies = await make_admin(db, redis, api)
    email = (await api.get("/admin/me", cookies=cookies)).json()["data"]["email"]
    api.cookies.clear()

    async def attempt(password: str, ip: str):
        return await api.post(
            "/admin/auth/login",
            json={"email": email, "password": password},
            headers={"X-Nf-Client-Connection-Ip": ip},
        )

    # A new address for every guess, so the per-address limit never triggers.
    for n in range(MAX_LOGIN_FAILURES_PER_ACCOUNT):
        assert (
            await attempt("wrong password", f"49.36.{n // 250}.{n % 250 + 1}")
        ).status_code == 401

    locked = await attempt(ADMIN_PASSWORD, "49.40.9.9")

    assert locked.status_code == 401  # even the right password, until the window ends


async def test_request_ids_from_callers_are_cleaned(api: AsyncClient) -> None:
    good = await api.get("/health", headers={"X-Request-ID": "abc12345-ok"})
    bad = await api.get("/health", headers={"X-Request-ID": "x\tforged line"})

    assert good.headers["x-request-id"] == "abc12345-ok"
    assert bad.headers["x-request-id"] != "x\tforged line"
