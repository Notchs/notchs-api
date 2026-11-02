"""Venue login on a real database: set password, login, lockout, forgot password, /venue/me."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.auth.models import AuthToken
from app.modules.auth.tokens import hash_token
from app.modules.auth.venue_auth import MAX_LOGIN_FAILURES, VenueAuthService
from app.modules.locations.models import City
from app.modules.venues.models import Venue, VenueMember

pytestmark = pytest.mark.asyncio(loop_scope="session")

PASSWORD = "filter coffee 1984"


@pytest.fixture
async def venue(db: AsyncSession) -> Venue:
    city = City(name="Ahmedabad", slug=f"ahd-{uuid.uuid4().hex[:6]}", state="Gujarat")
    db.add(city)
    await db.flush()
    row = Venue(city_id=city.id, name="Kahwa House", slug="kahwa-house", category="cafe")
    db.add(row)
    await db.flush()
    return row


@pytest.fixture
async def member(db: AsyncSession, venue: Venue) -> VenueMember:
    row = VenueMember(
        venue_id=venue.id,
        phone_e164="+919800000001",
        name="Riya",
        email=f"owner-{uuid.uuid4().hex[:6]}@kahwa.in",
    )
    db.add(row)
    await db.flush()
    return row


def token_from(text: str) -> str:
    link = re.search(r"https?://\S+", text)
    assert link, text
    assert link[0].startswith(f"{settings.VENUE_APP_URL}/set-password?token=")
    return parse_qs(urlsplit(link[0]).query)["token"][0]


async def welcome_token(db: AsyncSession, redis, member: VenueMember, venue: Venue) -> str:
    message = await VenueAuthService(db, redis).welcome_email(member, venue)
    await db.commit()
    assert message.to == member.email
    return token_from(message.text)


async def set_password(api: AsyncClient, token: str, password: str = PASSWORD) -> Response:
    return await api.post("/venue/auth/set-password", json={"token": token, "password": password})


async def login(api: AsyncClient, email: str, password: str = PASSWORD) -> Response:
    return await api.post("/venue/auth/login", json={"email": email, "password": password})


def cookie(response: Response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"{name} not set")


async def test_full_journey(api: AsyncClient, db, redis, member: VenueMember, venue: Venue) -> None:
    email = member.email
    token = await welcome_token(db, redis, member, venue)

    first = await set_password(api, token)
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["email"] == email
    assert data["venue"]["name"] == "Kahwa House"
    assert data["venue"]["venue_code"].startswith("VEN-")
    assert "access_expires_at" in data["session"]

    me = await api.get("/venue/me", cookies={"nx_venue_at": cookie(first, "nx_venue_at")})
    assert me.status_code == 200
    assert me.json()["data"]["id"] == str(member.id)

    again = await login(api, email.upper())
    assert again.status_code == 200
    rt = cookie(again, "nx_venue_rt")
    assert (await api.post("/auth/venue/refresh", cookies={"nx_venue_rt": rt})).status_code == 200
    await db.refresh(member)
    assert member.last_login_at is not None
    assert member.password_hash.startswith("$argon2id$")


async def test_me_needs_a_login(api: AsyncClient) -> None:
    response = await api.get("/venue/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.not_authenticated"


@pytest.mark.parametrize(
    "password", ["short", "x" * 129, "OWNER@KAHWA.IN"], ids=["short", "long", "email"]
)
async def test_weak_password_keeps_the_link_usable(
    api: AsyncClient, db, redis, member: VenueMember, venue: Venue, password: str
) -> None:
    member.email = "owner@kahwa.in"
    await db.commit()
    token = await welcome_token(db, redis, member, venue)

    refused = await set_password(api, token, password)

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] in {"auth.password_invalid", "validation_error"}
    assert (await set_password(api, token)).status_code == 200


async def test_link_works_once(api: AsyncClient, db, redis, member: VenueMember, venue) -> None:
    token = await welcome_token(db, redis, member, venue)

    assert (await set_password(api, token)).status_code == 200
    again = await set_password(api, token, "another long password")

    assert again.status_code == 400
    assert again.json()["error"]["code"] == "auth.token_invalid"


async def test_expired_link(api: AsyncClient, db, redis, member: VenueMember, venue) -> None:
    token = await welcome_token(db, redis, member, venue)
    await db.execute(
        update(AuthToken)
        .where(AuthToken.token_hash == hash_token(token))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()

    assert (await set_password(api, token)).status_code == 400


async def test_a_new_link_replaces_the_old_one(
    api: AsyncClient, db, redis, member: VenueMember, venue
) -> None:
    old = await welcome_token(db, redis, member, venue)
    new = await welcome_token(db, redis, member, venue)

    assert (await set_password(api, old)).status_code == 400
    assert (await set_password(api, new)).status_code == 200


async def test_spaces_in_passwords_are_kept(
    api: AsyncClient, db, redis, member: VenueMember, venue
) -> None:
    email = member.email
    await set_password(api, await welcome_token(db, redis, member, venue), "  spaced out pass  ")

    assert (await login(api, email, "spaced out pass")).status_code == 401
    assert (await login(api, email, "  spaced out pass  ")).status_code == 200


async def test_login_errors_look_the_same(
    api: AsyncClient, db, redis, member: VenueMember, venue
) -> None:
    email = member.email
    await set_password(api, await welcome_token(db, redis, member, venue))

    wrong_password = await login(api, email, "not the password")
    unknown_email = await login(api, "nobody@kahwa.in")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["error"]["code"] == "auth.invalid_credentials"


async def test_no_password_set_yet(api: AsyncClient, member: VenueMember) -> None:
    email = member.email
    assert (await login(api, email)).status_code == 401


async def test_lockout_after_repeated_failures(
    api: AsyncClient, db, redis, member: VenueMember, venue
) -> None:
    email = member.email
    await set_password(api, await welcome_token(db, redis, member, venue))

    for _ in range(MAX_LOGIN_FAILURES):
        assert (await login(api, email, "wrong password!")).status_code == 401
    locked = await login(api, email)

    assert locked.status_code == 401
    assert locked.json()["error"]["code"] == "auth.invalid_credentials"
    await redis.flushdb()  # the 15-minute window passes
    assert (await login(api, email)).status_code == 200


async def test_success_resets_the_failure_count(
    api: AsyncClient, db, redis, member: VenueMember, venue
) -> None:
    email = member.email
    await set_password(api, await welcome_token(db, redis, member, venue))

    for _ in range(MAX_LOGIN_FAILURES - 1):
        await login(api, email, "wrong password!")
    assert (await login(api, email)).status_code == 200
    for _ in range(MAX_LOGIN_FAILURES - 1):
        await login(api, email, "wrong password!")
    assert (await login(api, email)).status_code == 200


async def test_inactive_member_with_right_password(
    api: AsyncClient, db, redis, member: VenueMember, venue
) -> None:
    email = member.email
    await set_password(api, await welcome_token(db, redis, member, venue))
    member.is_active = False
    await db.commit()

    response = await login(api, email)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth.account_inactive"
    assert (await login(api, email, "wrong password!")).status_code == 401


async def forgot(api: AsyncClient, email: str) -> Response:
    return await api.post(
        "/venue/auth/forgot-password", json={"email": email, "turnstile_token": "ok"}
    )


async def test_forgot_password_same_answer_for_any_email(
    api: AsyncClient, db, redis, outbox, member: VenueMember, venue
) -> None:
    email = member.email
    await set_password(api, await welcome_token(db, redis, member, venue))

    known = await forgot(api, email)
    unknown = await forgot(api, "nobody@kahwa.in")

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert [m.to for m in outbox.sent] == [email]
    assert "Reset your Notchs password" in outbox.sent[0].subject


async def test_reset_link_logs_in_and_ends_other_sessions(
    api: AsyncClient, db, redis, outbox, member: VenueMember, venue
) -> None:
    email = member.email
    await set_password(api, await welcome_token(db, redis, member, venue))
    old_session = await login(api, email)
    old_rt = cookie(old_session, "nx_venue_rt")

    await forgot(api, email)
    reset = await set_password(api, token_from(outbox.sent[-1].text), "brand new password")

    assert reset.status_code == 200
    assert (await login(api, email)).status_code == 401  # old password
    assert (await login(api, email, "brand new password")).status_code == 200
    stale = await api.post("/auth/venue/refresh", cookies={"nx_venue_rt": old_rt})
    assert stale.status_code == 401


async def test_forgot_password_for_inactive_member_sends_nothing(
    api: AsyncClient, db, outbox, member: VenueMember
) -> None:
    email = member.email
    member.is_active = False
    await db.commit()

    assert (await forgot(api, email)).status_code == 202
    assert outbox.sent == []


async def test_forgot_password_rate_limit(api: AsyncClient, outbox, member: VenueMember) -> None:
    email = member.email
    for _ in range(3):
        assert (await forgot(api, email)).status_code == 202
    limited = await forgot(api, email)

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "auth.rate_limited"


async def test_forgot_password_needs_the_captcha(
    api: AsyncClient, turnstile, outbox, member: VenueMember
) -> None:
    email = member.email
    turnstile.passes = False

    response = await forgot(api, email)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "captcha.failed"
    assert outbox.sent == []


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "", "x" * 120 + "@kahwa.in"])
async def test_bad_email_format(api: AsyncClient, email: str) -> None:
    assert (await login(api, email)).status_code == 422
