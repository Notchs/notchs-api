"""Sessions on a real database: refresh rotation, reuse detection, logout, who-is-calling."""

import uuid
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

import pytest
from fastapi import APIRouter
from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.main import app
from app.modules.admin_users.models import AdminRole, AdminUser
from app.modules.admin_users.roles import built_in_role
from app.modules.auth.access_tokens import create_access_token
from app.modules.auth.dependencies import CurrentAdmin, CurrentUser, CurrentVenueMember
from app.modules.auth.enums import AccountStatus, RevokeReason, SubjectType
from app.modules.auth.exceptions import NotAuthenticatedException
from app.modules.auth.models import AuthToken
from app.modules.auth.sessions import IssuedSession, SessionService, Subject
from app.modules.auth.tokens import hash_token
from app.modules.locations.models import City
from app.modules.users.models import User
from app.modules.venues.models import Venue, VenueMember

# The database engine is session-scoped, so the tests must run on the same event loop.
pytestmark = pytest.mark.asyncio(loop_scope="session")

# A few routes that only exist in tests, to exercise the who-is-calling dependencies.
_probe = APIRouter(prefix=f"{settings.API_V1_PREFIX}/__test")


@_probe.get("/venue-me")
async def _venue_me(member: CurrentVenueMember) -> dict[str, str]:
    return {"id": str(member.id), "venue_id": str(member.venue_id)}


@_probe.get("/admin-me")
async def _admin_me(admin: CurrentAdmin) -> dict[str, str]:
    return {"id": str(admin.id)}


@_probe.get("/user-me")
async def _user_me(user: CurrentUser) -> dict[str, str]:
    return {"id": str(user.id)}


if not any(getattr(r, "path", "").startswith(_probe.prefix) for r in app.routes):
    app.include_router(_probe)


@pytest.fixture
async def member(db: AsyncSession) -> VenueMember:
    city = City(name="Ahmedabad", slug=f"ahd-{uuid.uuid4().hex[:6]}", state="Gujarat")
    db.add(city)
    await db.flush()
    venue = Venue(city_id=city.id, name="Kahwa", slug="kahwa", category="cafe")
    db.add(venue)
    await db.flush()
    row = VenueMember(
        venue_id=venue.id,
        phone_e164="+919800000001",
        name="Owner",
        email=f"owner-{uuid.uuid4().hex[:6]}@kahwa.in",
        status=AccountStatus.ACTIVE,
        password_hash="not-a-real-hash",
    )
    db.add(row)
    await db.flush()
    return row


@pytest.fixture
async def admin(db: AsyncSession) -> AdminUser:
    row = AdminUser(
        name="Abhishek",
        email=f"a-{uuid.uuid4().hex[:6]}@notchs.com",
        role=await built_in_role(db, AdminRole.ADMIN),
        status=AccountStatus.ACTIVE,
        password_hash="not-a-real-hash",
    )
    db.add(row)
    await db.flush()
    return row


@pytest.fixture
async def user(db: AsyncSession) -> User:
    row = User(phone_e164=f"+9198{uuid.uuid4().int % 10**8:08d}")
    db.add(row)
    await db.flush()
    return row


def venue_subject(member: VenueMember) -> Subject:
    return Subject(SubjectType.VENUE_MEMBER, member.id, venue_id=member.venue_id)


def set_cookies(response: Response) -> dict[str, SimpleCookie]:
    """Set-Cookie headers by cookie name, with their attributes."""
    found = {}
    for header in response.headers.get_list("set-cookie"):
        jar = SimpleCookie()
        jar.load(header)
        for name in jar:
            found[name] = jar
    return found


async def refresh(api: AsyncClient, audience: str, refresh_token: str) -> Response:
    return await api.post(f"/auth/{audience}/refresh", cookies={f"nx_{audience}_rt": refresh_token})


async def stored(db: AsyncSession, raw: str) -> AuthToken:
    token = await db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(raw)))
    assert token is not None
    await db.refresh(token)
    return token


async def start(db: AsyncSession, subject: Subject) -> IssuedSession:
    issued = await SessionService(db).start(subject, ip="203.0.113.7", user_agent="pytest")
    await db.commit()
    return issued


async def test_start_stores_only_a_hash(db: AsyncSession, member: VenueMember) -> None:
    issued = await start(db, venue_subject(member))
    token = await stored(db, issued.refresh_token)

    assert token.token_hash != issued.refresh_token
    assert (token.subject_type, token.venue_member_id) == ("venue_member", member.id)
    assert token.family_id is not None
    assert str(token.ip_address) == "203.0.113.7"


async def test_refresh_rotates_and_sets_cookies(
    api: AsyncClient, db: AsyncSession, member: VenueMember
) -> None:
    issued = await start(db, venue_subject(member))

    response = await refresh(api, "venue", issued.refresh_token)

    assert response.status_code == 200, response.text
    assert "access_expires_at" in response.json()["data"]
    cookies = set_cookies(response)
    access, rt = cookies["nx_venue_at"]["nx_venue_at"], cookies["nx_venue_rt"]["nx_venue_rt"]
    assert rt.value != issued.refresh_token
    assert access["path"] == "/" and access["httponly"] and access["samesite"].lower() == "lax"
    assert rt["path"] == f"{settings.API_V1_PREFIX}/auth/venue" and rt["httponly"]
    assert int(access["max-age"]) <= settings.ACCESS_TOKEN_TTL_MINUTES * 60
    old, new = await stored(db, issued.refresh_token), await stored(db, rt.value)
    assert old.used_at is not None and new.used_at is None
    assert old.family_id == new.family_id


async def test_reuse_after_grace_revokes_the_whole_family(
    api: AsyncClient, db: AsyncSession, member: VenueMember
) -> None:
    issued = await start(db, venue_subject(member))
    rotated = set_cookies(await refresh(api, "venue", issued.refresh_token))
    new_rt = rotated["nx_venue_rt"]["nx_venue_rt"].value
    # Pretend the first refresh happened a minute ago, then replay the old token.
    await db.execute(
        update(AuthToken)
        .where(AuthToken.token_hash == hash_token(issued.refresh_token))
        .values(used_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db.commit()

    replay = await refresh(api, "venue", issued.refresh_token)
    after = await refresh(api, "venue", new_rt)

    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "auth.not_authenticated"
    assert after.status_code == 401  # the thief's copy and the real one are both dead
    assert (await stored(db, new_rt)).revoked_at is not None
    assert (await stored(db, new_rt)).revoke_reason == RevokeReason.REUSE_DETECTED


async def test_reuse_within_grace_fails_without_logging_out(
    api: AsyncClient, db: AsyncSession, member: VenueMember
) -> None:
    issued = await start(db, venue_subject(member))
    first = set_cookies(await refresh(api, "venue", issued.refresh_token))
    new_rt = first["nx_venue_rt"]["nx_venue_rt"].value

    second_tab = await refresh(api, "venue", issued.refresh_token)

    assert second_tab.status_code == 401
    assert (await refresh(api, "venue", new_rt)).status_code == 200


async def test_expired_refresh_token(
    api: AsyncClient, db: AsyncSession, member: VenueMember
) -> None:
    issued = await start(db, venue_subject(member))
    await db.execute(
        update(AuthToken)
        .where(AuthToken.token_hash == hash_token(issued.refresh_token))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()

    assert (await refresh(api, "venue", issued.refresh_token)).status_code == 401


@pytest.mark.parametrize("raw", ["", "made-up-token"])
async def test_missing_or_unknown_refresh_cookie(api: AsyncClient, raw: str) -> None:
    response = await refresh(api, "venue", raw)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.not_authenticated"


async def test_refresh_token_only_works_for_its_audience(
    api: AsyncClient, db: AsyncSession, member: VenueMember
) -> None:
    issued = await start(db, venue_subject(member))

    assert (await refresh(api, "admin", issued.refresh_token)).status_code == 401
    with pytest.raises(NotAuthenticatedException):
        await SessionService(db).refresh(issued.refresh_token, SubjectType.ADMIN)


async def test_deactivated_member_cannot_refresh(
    api: AsyncClient, db: AsyncSession, member: VenueMember
) -> None:
    issued = await start(db, venue_subject(member))
    member.status = AccountStatus.DISABLED
    await db.commit()

    assert (await refresh(api, "venue", issued.refresh_token)).status_code == 401
    assert (await stored(db, issued.refresh_token)).revoked_at is not None


async def test_logout_revokes_and_clears_cookies(
    api: AsyncClient, db: AsyncSession, member: VenueMember
) -> None:
    issued = await start(db, venue_subject(member))

    response = await api.post("/auth/venue/logout", cookies={"nx_venue_rt": issued.refresh_token})

    assert response.status_code == 204
    cleared = set_cookies(response)
    assert cleared["nx_venue_at"]["nx_venue_at"]["max-age"] == "0"
    assert cleared["nx_venue_rt"]["nx_venue_rt"]["path"] == f"{settings.API_V1_PREFIX}/auth/venue"
    assert (await refresh(api, "venue", issued.refresh_token)).status_code == 401
    assert (await stored(db, issued.refresh_token)).revoke_reason == RevokeReason.LOGOUT


async def test_logout_without_a_session_still_succeeds(api: AsyncClient) -> None:
    assert (await api.post("/auth/venue/logout")).status_code == 204


async def test_end_all_can_keep_the_current_session(db: AsyncSession, member: VenueMember) -> None:
    subject = venue_subject(member)
    laptop, phone = await start(db, subject), await start(db, subject)
    laptop_family = (await stored(db, laptop.refresh_token)).family_id

    revoked = await SessionService(db).end_all(
        subject, RevokeReason.PASSWORD_CHANGED, keep_family=laptop_family
    )
    await db.commit()

    assert revoked == 1
    assert (await stored(db, laptop.refresh_token)).revoked_at is None
    assert (await stored(db, phone.refresh_token)).revoked_at is not None


async def test_admin_and_user_audiences(
    api: AsyncClient, db: AsyncSession, admin: AdminUser, user: User
) -> None:
    admin_session = await start(db, Subject(SubjectType.ADMIN, admin.id))
    user_session = await start(db, Subject(SubjectType.USER, user.id))

    assert (await refresh(api, "admin", admin_session.refresh_token)).status_code == 200
    assert (await refresh(api, "user", user_session.refresh_token)).status_code == 200


# --- who is calling ---------------------------------------------------------------------


async def test_current_venue_member(
    api: AsyncClient, db: AsyncSession, member: VenueMember
) -> None:
    issued = await start(db, venue_subject(member))

    response = await api.get("/__test/venue-me", cookies={"nx_venue_at": issued.access_token})

    assert response.status_code == 200
    assert response.json() == {"id": str(member.id), "venue_id": str(member.venue_id)}


@pytest.mark.parametrize(
    "cookies",
    [
        {},
        {"nx_venue_at": "garbage"},
        {"nx_venue_at": create_access_token(SubjectType.USER, uuid.uuid4())},
        {
            "nx_venue_at": create_access_token(
                SubjectType.VENUE_MEMBER, uuid.uuid4(), now=datetime.now(UTC) - timedelta(hours=1)
            )
        },
    ],
)
async def test_current_venue_member_rejects_bad_cookies(
    api: AsyncClient, cookies: dict[str, str]
) -> None:
    response = await api.get("/__test/venue-me", cookies=cookies)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.not_authenticated"


async def test_inactive_member_or_deleted_venue_is_refused(
    api: AsyncClient, db: AsyncSession, member: VenueMember
) -> None:
    issued = await start(db, venue_subject(member))
    cookies = {"nx_venue_at": issued.access_token}
    await db.execute(
        update(Venue).where(Venue.id == member.venue_id).values(deleted_at=datetime.now(UTC))
    )
    await db.commit()

    response = await api.get("/__test/venue-me", cookies=cookies)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth.account_inactive"


async def test_current_admin_and_user(
    api: AsyncClient, db: AsyncSession, admin: AdminUser, user: User
) -> None:
    admin_at = (await start(db, Subject(SubjectType.ADMIN, admin.id))).access_token
    user_at = (await start(db, Subject(SubjectType.USER, user.id))).access_token

    assert (await api.get("/__test/admin-me", cookies={"nx_admin_at": admin_at})).status_code == 200
    assert (await api.get("/__test/user-me", cookies={"nx_user_at": user_at})).status_code == 200
    # An admin's cookie is not a user's cookie.
    assert (await api.get("/__test/user-me", cookies={"nx_user_at": admin_at})).status_code == 401
