"""Step 6 on a real database: change password (venue and admin) and change email (venue)."""

import re
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.admin_users.models import AdminRole, AdminUser
from app.modules.admin_users.roles import built_in_role
from app.modules.auth.admin_auth import AdminAuthService
from app.modules.auth.password_auth import MAX_LOGIN_FAILURES
from app.modules.auth.venue_auth import VenueAuthService
from app.modules.locations.models import City
from app.modules.venues.models import Venue, VenueMember

pytestmark = pytest.mark.asyncio(loop_scope="session")

OLD = "old password 1984"
NEW = "new password 2026"


def token_from(text: str) -> str:
    link = re.search(r"https?://\S+", text)
    assert link, text
    return parse_qs(urlsplit(link[0]).query)["token"][0]


def cookie(response: Response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"{name} not set")


@pytest.fixture
async def owner(db: AsyncSession, redis, api: AsyncClient) -> dict[str, str]:
    """A venue owner with password OLD, logged in on two devices. Returns emails and cookies."""
    city = City(name="Ahmedabad", slug=f"ahd-{uuid.uuid4().hex[:6]}", state="Gujarat")
    db.add(city)
    await db.flush()
    venue = Venue(city_id=city.id, name="Kahwa House", slug="kahwa-house", category="cafe")
    db.add(venue)
    await db.flush()
    email = f"owner-{uuid.uuid4().hex[:6]}@kahwa.in"
    member = VenueMember(venue_id=venue.id, phone_e164="+919800000001", name="Riya", email=email)
    db.add(member)
    await db.flush()
    welcome = await VenueAuthService(db, redis).welcome_email(member, venue)
    await db.commit()
    first = await api.post(
        "/venue/auth/set-password", json={"token": token_from(welcome.text), "password": OLD}
    )
    assert first.status_code == 200
    laptop = await api.post("/venue/auth/login", json={"email": email, "password": OLD})
    phone = await api.post("/venue/auth/login", json={"email": email, "password": OLD})
    api.cookies.clear()
    return {
        "email": email,
        "at": cookie(laptop, "nx_venue_at"),
        "laptop_rt": cookie(laptop, "nx_venue_rt"),
        "phone_rt": cookie(phone, "nx_venue_rt"),
    }


async def login(api: AsyncClient, email: str, password: str) -> int:
    response = await api.post("/venue/auth/login", json={"email": email, "password": password})
    api.cookies.clear()
    return response.status_code


async def refresh(api: AsyncClient, rt: str) -> int:
    response = await api.post("/auth/venue/refresh", cookies={"nx_venue_rt": rt})
    api.cookies.clear()
    return response.status_code


# --- change password ------------------------------------------------------------------------


async def change_password(api: AsyncClient, at: str, current: str, new: str) -> Response:
    response = await api.post(
        "/venue/auth/change-password",
        json={"current_password": current, "new_password": new},
        cookies={"nx_venue_at": at},
    )
    return response


async def test_change_password(api: AsyncClient, outbox, owner) -> None:
    response = await change_password(api, owner["at"], OLD, NEW)

    assert response.status_code == 200, response.text
    this_device_rt = cookie(response, "nx_venue_rt")  # new cookies for this device
    api.cookies.clear()
    assert await login(api, owner["email"], OLD) == 401
    assert await login(api, owner["email"], NEW) == 200
    assert await refresh(api, this_device_rt) == 200  # this device stays logged in
    assert await refresh(api, owner["phone_rt"]) == 401  # the other device is logged out
    assert await refresh(api, owner["laptop_rt"]) == 401  # the old cookie is replaced
    notice = outbox.sent[-1]
    assert notice.to == owner["email"] and "password was changed" in notice.subject
    assert f"{settings.VENUE_APP_URL}/forgot-password" in notice.text


async def test_wrong_current_password(api: AsyncClient, outbox, owner) -> None:
    sent = len(outbox.sent)

    response = await change_password(api, owner["at"], "not my password", NEW)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "auth.current_password_invalid"
    assert await login(api, owner["email"], OLD) == 200
    assert len(outbox.sent) == sent


async def test_wrong_current_password_lockout(api: AsyncClient, owner) -> None:
    for _ in range(MAX_LOGIN_FAILURES):
        await change_password(api, owner["at"], "not my password", NEW)

    locked = await change_password(api, owner["at"], OLD, NEW)

    assert locked.status_code == 429


@pytest.mark.parametrize("new", ["short", OLD], ids=["policy", "same-as-current"])
async def test_new_password_rules(api: AsyncClient, owner, new: str) -> None:
    response = await change_password(api, owner["at"], OLD, new)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "auth.password_invalid"


async def test_change_password_needs_a_login(api: AsyncClient) -> None:
    response = await api.post(
        "/venue/auth/change-password", json={"current_password": OLD, "new_password": NEW}
    )

    assert response.status_code == 401


async def test_admin_change_password(db: AsyncSession, redis, api: AsyncClient, outbox) -> None:
    admin = AdminUser(
        name="Abhishek",
        email=f"a-{uuid.uuid4().hex[:6]}@notchs.com",
        role=await built_in_role(db, AdminRole.ADMIN),
    )
    db.add(admin)
    await db.flush()
    email = admin.email
    invite = await AdminAuthService(db, redis).invite_email(admin)
    await db.commit()
    first = await api.post(
        "/admin/auth/set-password", json={"token": token_from(invite.text), "password": OLD}
    )
    api.cookies.clear()

    response = await api.post(
        "/admin/auth/change-password",
        json={"current_password": OLD, "new_password": NEW},
        cookies={"nx_admin_at": cookie(first, "nx_admin_at")},
    )
    api.cookies.clear()

    assert response.status_code == 200
    login_new = await api.post("/admin/auth/login", json={"email": email, "password": NEW})
    assert login_new.status_code == 200
    assert f"{settings.ADMIN_APP_URL}/forgot-password" in outbox.sent[-1].text


# --- change email ---------------------------------------------------------------------------


async def send_code(api: AsyncClient, at: str, new_email: str, password: str = OLD) -> Response:
    return await api.post(
        "/venue/me/email/send-code",
        json={"new_email": new_email, "current_password": password},
        cookies={"nx_venue_at": at},
    )


async def verify(api: AsyncClient, at: str, code: str) -> Response:
    return await api.post(
        "/venue/me/email/verify", json={"code": code}, cookies={"nx_venue_at": at}
    )


def code_in(text: str) -> str:
    match = re.search(r"\b(\d{6})\b", text)
    assert match, text
    return match[1]


async def test_change_email(api: AsyncClient, outbox, owner) -> None:
    new_email = f"new-{uuid.uuid4().hex[:6]}@kahwa.in"

    sent = await send_code(api, owner["at"], new_email.upper())
    code_mail = outbox.sent[-1]
    done = await verify(api, owner["at"], code_in(code_mail.text))

    assert sent.status_code == 202
    assert code_mail.to == new_email  # the code goes to the NEW address
    assert done.status_code == 200, done.text
    assert done.json()["data"]["email"] == new_email
    notice = outbox.sent[-1]
    assert notice.to == owner["email"] and new_email in notice.text  # the OLD address is told
    assert await login(api, new_email, OLD) == 200
    assert await login(api, owner["email"], OLD) == 401
    assert await refresh(api, owner["phone_rt"]) == 200  # sessions stay


async def test_change_email_wrong_password(api: AsyncClient, outbox, owner) -> None:
    sent = len(outbox.sent)

    response = await send_code(api, owner["at"], "new@kahwa.in", "not my password")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "auth.current_password_invalid"
    assert len(outbox.sent) == sent


async def test_wrong_code_and_no_pending_change(api: AsyncClient, outbox, owner) -> None:
    nothing_pending = await verify(api, owner["at"], "123456")
    await send_code(api, owner["at"], f"new-{uuid.uuid4().hex[:6]}@kahwa.in")
    real = code_in(outbox.sent[-1].text)
    wrong = await verify(api, owner["at"], "000000" if real != "000000" else "111111")

    assert nothing_pending.status_code == wrong.status_code == 400
    assert wrong.json()["error"]["code"] == "auth.code_invalid"
    assert (await verify(api, owner["at"], real)).status_code == 200


async def test_taken_email_gets_the_same_answer_and_no_code(
    api: AsyncClient, db: AsyncSession, outbox, owner
) -> None:
    city = City(name="Surat", slug=f"srt-{uuid.uuid4().hex[:6]}", state="Gujarat")
    db.add(city)
    await db.flush()
    other_venue = Venue(city_id=city.id, name="Other", slug="other", category="bar")
    db.add(other_venue)
    await db.flush()
    taken = f"taken-{uuid.uuid4().hex[:6]}@kahwa.in"
    db.add(VenueMember(venue_id=other_venue.id, phone_e164="+919800000002", name="X", email=taken))
    await db.commit()
    sent = len(outbox.sent)

    taken_answer = await send_code(api, owner["at"], taken)
    free_answer = await send_code(api, owner["at"], f"free-{uuid.uuid4().hex[:6]}@kahwa.in")

    assert taken_answer.status_code == free_answer.status_code == 202
    assert taken_answer.json() == free_answer.json()
    assert len(outbox.sent) == sent + 1  # only the free address got a code


@pytest.mark.parametrize(
    ("new_email", "code"),
    [("SAME", "auth.email_unchanged"), ("x@mailinator.com", "auth.email_not_allowed")],
)
async def test_change_email_refusals(api: AsyncClient, owner, new_email: str, code: str) -> None:
    response = await send_code(
        api, owner["at"], owner["email"] if new_email == "SAME" else new_email
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


async def test_asking_again_replaces_the_pending_address(api: AsyncClient, outbox, owner) -> None:
    first, second = (f"{n}-{uuid.uuid4().hex[:6]}@kahwa.in" for n in ("first", "second"))
    await send_code(api, owner["at"], first)
    await send_code(api, owner["at"], second)

    done = await verify(api, owner["at"], code_in(outbox.sent[-1].text))

    assert done.json()["data"]["email"] == second
