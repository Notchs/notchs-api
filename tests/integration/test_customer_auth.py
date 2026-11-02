"""Customer login with the WhatsApp link, on a real database."""

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.auth.customer_auth import MAX_LINKS_PER_WINDOW, CustomerAuthService
from app.modules.auth.exceptions import AuthRateLimitedException, TokenInvalidException
from app.modules.auth.models import AuthToken
from app.modules.auth.tokens import hash_token
from app.modules.users.models import User, UserStatus

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def user(db: AsyncSession) -> User:
    row = User(phone_e164=f"+9198{uuid.uuid4().int % 10**8:08d}", name="Riya", handle=None)
    db.add(row)
    await db.commit()
    return row


async def link_for(db: AsyncSession, redis, user: User) -> str:
    link = await CustomerAuthService(db, redis).issue_login_link(user)
    await db.commit()
    return link


def token_of(link: str) -> str:
    assert link.startswith(f"{settings.CUSTOMER_APP_URL}/login/verify?token="), link
    return parse_qs(urlsplit(link).query)["token"][0]


async def consume(api: AsyncClient, token: str) -> Response:
    return await api.post("/auth/customer/consume", json={"token": token})


def cookie(response: Response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"{name} not set")


async def test_link_logs_in(api: AsyncClient, db, redis, user: User) -> None:
    user_id = user.id
    token = token_of(await link_for(db, redis, user))

    response = await consume(api, token)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["id"] == str(user_id) and data["name"] == "Riya" and data["is_public"] is False
    assert "phone" not in response.text and "+9198" not in response.text
    await db.refresh(user)
    assert user.last_seen_at is not None


async def test_session_cookies_refresh_and_log_out(api: AsyncClient, db, redis, user: User) -> None:
    response = await consume(api, token_of(await link_for(db, redis, user)))
    rt = cookie(response, "nx_user_rt")
    assert cookie(response, "nx_user_at")

    refreshed = await api.post("/auth/user/refresh", cookies={"nx_user_rt": rt})
    new_rt = cookie(refreshed, "nx_user_rt")
    logout = await api.post("/auth/user/logout", cookies={"nx_user_rt": new_rt})
    after = await api.post("/auth/user/refresh", cookies={"nx_user_rt": new_rt})

    assert refreshed.status_code == 200
    assert logout.status_code == 204
    assert after.status_code == 401


async def test_link_works_once(api: AsyncClient, db, redis, user: User) -> None:
    token = token_of(await link_for(db, redis, user))

    assert (await consume(api, token)).status_code == 200
    again = await consume(api, token)

    assert again.status_code == 400
    assert again.json()["error"]["code"] == "auth.token_invalid"


async def test_link_expires(api: AsyncClient, db, redis, user: User) -> None:
    token = token_of(await link_for(db, redis, user))
    await db.execute(
        update(AuthToken)
        .where(AuthToken.token_hash == hash_token(token))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()

    assert (await consume(api, token)).status_code == 400


async def test_new_link_cancels_the_old_one(api: AsyncClient, db, redis, user: User) -> None:
    old = token_of(await link_for(db, redis, user))
    new = token_of(await link_for(db, redis, user))

    assert (await consume(api, old)).status_code == 400
    assert (await consume(api, new)).status_code == 200


@pytest.mark.parametrize("status", [UserStatus.BLOCKED, UserStatus.DELETED])
async def test_blocked_or_deleted_customer_gets_the_same_error(
    api: AsyncClient, db, redis, user: User, status: UserStatus
) -> None:
    token = token_of(await link_for(db, redis, user))
    user.status = status
    await db.commit()

    response = await consume(api, token)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "auth.token_invalid"


async def test_no_link_for_a_blocked_customer(db, redis, user: User) -> None:
    user.status = UserStatus.BLOCKED
    await db.commit()

    with pytest.raises(TokenInvalidException):
        await CustomerAuthService(db, redis).issue_login_link(user)


async def test_link_rate_limit(db, redis, user: User) -> None:
    service = CustomerAuthService(db, redis)
    for _ in range(MAX_LINKS_PER_WINDOW):
        await service.issue_login_link(user)

    with pytest.raises(AuthRateLimitedException):
        await service.issue_login_link(user)


@pytest.mark.parametrize("token", ["x" * 43, "short"])
async def test_made_up_tokens(api: AsyncClient, token: str) -> None:
    response = await consume(api, token)

    assert response.status_code in {400, 422}


async def test_a_venue_link_is_not_a_customer_link(api: AsyncClient, db, redis) -> None:
    """Tokens are tied to their kind: a password-reset style token can't log a customer in."""
    import re

    from app.modules.auth.venue_auth import VenueAuthService
    from app.modules.locations.models import City
    from app.modules.venues.models import Venue, VenueMember

    city = City(name="Ahmedabad", slug=f"ahd-{uuid.uuid4().hex[:6]}", state="Gujarat")
    db.add(city)
    await db.flush()
    venue = Venue(city_id=city.id, name="Kahwa", slug="kahwa", category="cafe")
    db.add(venue)
    await db.flush()
    member = VenueMember(
        venue_id=venue.id,
        phone_e164="+919800000001",
        name="R",
        email=f"o-{uuid.uuid4().hex[:6]}@kahwa.in",
    )
    db.add(member)
    await db.flush()
    welcome = await VenueAuthService(db, redis).welcome_email(member, venue)
    await db.commit()
    venue_token = parse_qs(urlsplit(re.search(r"https?://\S+", welcome.text)[0]).query)["token"][0]

    assert (await consume(api, venue_token)).status_code == 400
