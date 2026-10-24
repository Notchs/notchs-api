"""`POST /dev/simulate-inbound` on a real database."""

import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.admin_users.models import AdminRole
from app.modules.users.models import User
from app.modules.whatsapp.bot import HELP_REPLY, LOGIN_BUTTON
from app.modules.whatsapp.models import WhatsAppMessage
from tests.integration.test_admin import make_admin

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def admin(db: AsyncSession, redis: FakeAsyncRedis, api: AsyncClient) -> dict[str, str]:
    return await make_admin(db, redis, api)


def number() -> str:
    return f"98{uuid.uuid4().int % 10**8:08d}"


async def simulate(api: AsyncClient, cookies: dict[str, str], phone: str, text: str):
    return await api.post(
        "/dev/simulate-inbound", json={"phone": phone, "text": text}, cookies=cookies
    )


async def test_login_from_a_new_number(
    api: AsyncClient, db: AsyncSession, admin: dict[str, str]
) -> None:
    # The endpoint brings its own bot: the api fixture's recording bot is never used.
    local = number()

    response = await simulate(api, admin, local, "LOGIN")  # typed the Indian way

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    user = await db.scalar(select(User).where(User.phone_e164 == f"+91{local}"))
    assert user is not None and data["customer_id"] == str(user.id)
    (reply,) = data["replies"]
    link = reply.rsplit("\n", 1)[1]
    assert link.startswith(f"{settings.CUSTOMER_APP_URL}/login/verify?token=")

    login = await api.post(
        "/auth/customer/consume", json={"token": parse_qs(urlsplit(link).query)["token"][0]}
    )
    assert login.status_code == 200, login.text

    inbound = await db.scalar(
        select(WhatsAppMessage)
        .where(WhatsAppMessage.id == uuid.UUID(data["message_id"]))
        .execution_options(populate_existing=True)
    )
    assert inbound is not None
    assert inbound.wa_message_id.startswith("sim:")
    assert inbound.processed_at is not None


async def test_other_text_gets_help_once(api: AsyncClient, admin: dict[str, str]) -> None:
    phone = f"+91{number()}"

    first = await simulate(api, admin, phone, "hi")
    again = await simulate(api, admin, phone, "hello?")

    assert first.json()["data"]["replies"] == [f"{HELP_REPLY}\n[{LOGIN_BUTTON.title}]"]
    assert again.json()["data"]["replies"] == []  # the 10-minute limit, as on WhatsApp


async def test_admins_only(api: AsyncClient, db: AsyncSession, redis: FakeAsyncRedis) -> None:
    reviewer = await make_admin(db, redis, api, AdminRole.REVIEWER)
    assert (await simulate(api, reviewer, number(), "LOGIN")).status_code == 403

    api.cookies.clear()  # the client keeps the cookies from logging the reviewer in
    assert (await simulate(api, {}, number(), "LOGIN")).status_code == 401


async def test_tapping_the_login_button(api: AsyncClient, admin: dict[str, str]) -> None:
    phone = f"+91{number()}"

    response = await api.post(
        "/dev/simulate-inbound", json={"phone": phone, "button": LOGIN_BUTTON.id}, cookies=admin
    )

    assert response.status_code == 200, response.text
    (reply,) = response.json()["data"]["replies"]
    assert "/login/verify?token=" in reply


@pytest.mark.parametrize(
    "body",
    [
        {"phone": "12", "text": "LOGIN"},
        {"phone": "9876543210", "text": ""},
        {"text": "LOGIN"},
        {"phone": "9876543210"},
        {"phone": "9876543210", "text": "LOGIN", "button": "login"},
    ],
    ids=["bad-number", "empty-text", "no-number", "neither", "both"],
)
async def test_invalid_input(api: AsyncClient, admin: dict[str, str], body: dict[str, str]) -> None:
    response = await api.post("/dev/simulate-inbound", json=body, cookies=admin)

    assert response.status_code == 422


async def test_not_in_production(
    api: AsyncClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    response = await simulate(api, admin, number(), "LOGIN")

    assert response.status_code == 404
