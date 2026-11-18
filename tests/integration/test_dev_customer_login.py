"""`POST /dev/login-as-customer`: log in as a test customer from the API docs. Not in production."""

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.modules.admin_users.models import AdminRole
from app.modules.users.models import UserStatus
from tests.integration.test_admin import cookie, make_admin
from tests.integration.test_scan_bot import add_customer

pytestmark = pytest.mark.asyncio(loop_scope="session")

URL = "/dev/login-as-customer"


async def test_an_admin_logs_in_as_a_customer_and_calls_their_endpoints(
    api: AsyncClient, db, redis
) -> None:
    admin = await make_admin(db, redis, api)
    customer = await add_customer(db)

    # Any format of the number works, as in the bot simulator.
    response = await api.post(URL, json={"phone": customer.phone_e164[3:]}, cookies=admin)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert (data["id"], data["handle"]) == (str(customer.id), customer.handle)
    assert "phone" not in str(data).lower()  # never the number
    me = await api.get("/me", cookies={"nx_user_at": cookie(response, "nx_user_at")})
    assert me.status_code == 200, me.text
    assert me.json()["data"]["handle"] == customer.handle


@pytest.mark.parametrize("case", ["unknown", "not_agreed", "blocked"])
async def test_only_active_customers_who_agreed(api: AsyncClient, db, redis, case: str) -> None:
    admin = await make_admin(db, redis, api)
    phone = "+919800000777"
    if case == "not_agreed":
        phone = (await add_customer(db, agreed=False)).phone_e164
    elif case == "blocked":
        phone = (await add_customer(db, status=UserStatus.BLOCKED)).phone_e164

    response = await api.post(URL, json={"phone": phone}, cookies=admin)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dev.customer_not_found"
    assert "nx_user_at" not in response.headers.get("set-cookie", "")


async def test_admins_only(api: AsyncClient, db, redis) -> None:
    customer = await add_customer(db)
    reviewer = await make_admin(db, redis, api, AdminRole.REVIEWER)
    api.cookies.clear()

    nobody = await api.post(URL, json={"phone": customer.phone_e164})
    as_reviewer = await api.post(URL, json={"phone": customer.phone_e164}, cookies=reviewer)

    assert nobody.status_code == 401
    assert as_reviewer.status_code == 403


async def test_same_limit_as_real_login_links(api: AsyncClient, db, redis) -> None:
    admin = await make_admin(db, redis, api)
    customer = await add_customer(db)

    answers = [
        (await api.post(URL, json={"phone": customer.phone_e164}, cookies=admin)).status_code
        for _ in range(4)
    ]

    assert answers == [200, 200, 200, 429]  # 3 links per 15 minutes


async def test_not_in_production(api: AsyncClient, db, redis, monkeypatch) -> None:
    admin = await make_admin(db, redis, api)
    customer = await add_customer(db)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    response = await api.post(URL, json={"phone": customer.phone_e164}, cookies=admin)

    assert response.status_code == 404
