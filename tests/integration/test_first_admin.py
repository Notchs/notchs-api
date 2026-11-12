"""The admin made from FIRST_ADMIN_EMAIL (+ FIRST_ADMIN_PASSWORD): created once, never reset."""

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from app.clients.email import EmailSendError
from app.core.config import settings
from app.modules.admin_users.bootstrap import ensure_first_admin
from app.modules.admin_users.models import AdminRole, AdminUser
from tests.integration.test_admin import make_admin

pytestmark = pytest.mark.asyncio(loop_scope="session")

EMAIL = "founder@notchs.test"
PASSWORD = "a long first password"


@pytest.fixture
def first_admin(monkeypatch):
    """FIRST_ADMIN_EMAIL set, no password; tests add one where they need it."""
    monkeypatch.setattr(settings, "FIRST_ADMIN_EMAIL", EMAIL)
    monkeypatch.setattr(settings, "FIRST_ADMIN_NAME", "Harsh")
    monkeypatch.setattr(settings, "FIRST_ADMIN_PASSWORD", None)

    def with_password(value: str = PASSWORD) -> None:
        monkeypatch.setattr(settings, "FIRST_ADMIN_PASSWORD", SecretStr(value))

    return with_password


async def no_admins(db) -> None:
    await db.execute(delete(AdminUser))
    await db.commit()


async def founder(db) -> AdminUser | None:
    return await db.scalar(
        select(AdminUser).where(AdminUser.email == EMAIL).execution_options(populate_existing=True)
    )


async def login(api, password: str = PASSWORD):
    api.cookies.clear()
    return await api.post("/admin/auth/login", json={"email": EMAIL, "password": password})


async def test_does_nothing_when_unset(db, redis, outbox, monkeypatch) -> None:
    monkeypatch.setattr(settings, "FIRST_ADMIN_EMAIL", None)
    await no_admins(db)

    assert await ensure_first_admin(db, redis, outbox) is False
    assert await db.scalar(select(func.count()).select_from(AdminUser)) == 0
    assert outbox.sent == []


async def test_with_a_password_logs_in_without_any_email(
    api, db, redis, outbox, first_admin
) -> None:
    first_admin()
    await no_admins(db)

    created = await ensure_first_admin(db, redis, outbox)
    again = await ensure_first_admin(db, redis, outbox)

    assert (created, again) == (True, False)
    admin = await founder(db)
    assert admin is not None
    assert (admin.name, admin.role, admin.is_active) == ("Harsh", AdminRole.ADMIN, True)
    assert outbox.sent == []  # no email needed
    response = await login(api)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["role"] == "admin"
    assert PASSWORD not in (admin.password_hash or "")


async def test_without_a_password_emails_a_link(db, redis, outbox, first_admin) -> None:
    await no_admins(db)

    assert await ensure_first_admin(db, redis, outbox) is True

    assert (await founder(db)).password_hash is None
    assert len(outbox.sent) == 1
    assert outbox.sent[0].to == EMAIL
    assert f"{settings.ADMIN_APP_URL}/set-password?token=" in outbox.sent[0].text


async def test_a_lost_link_is_rescued_by_adding_the_password(
    api, db, redis, outbox, first_admin
) -> None:
    """What happened on the first deploy: the account was made, the email never arrived."""

    class BrokenEmail:
        async def send(self, message) -> None:
            raise EmailSendError("Resend returned 403: domain is not verified")

    await no_admins(db)
    assert await ensure_first_admin(db, redis, BrokenEmail()) is True  # created, email failed
    assert (await login(api)).status_code == 401

    first_admin()
    assert await ensure_first_admin(db, redis, outbox) is True

    assert (await login(api)).status_code == 200


async def test_other_admins_do_not_block_it(api, db, redis, outbox, first_admin) -> None:
    await make_admin(db, redis, api)  # e.g. an old account nobody can log in to
    first_admin()

    assert await ensure_first_admin(db, redis, outbox) is True
    assert (await login(api)).status_code == 200


async def test_never_resets_a_password_or_revives_an_account(
    api, db, redis, outbox, first_admin
) -> None:
    first_admin()
    await no_admins(db)
    await ensure_first_admin(db, redis, outbox)

    first_admin("a different password")
    assert await ensure_first_admin(db, redis, outbox) is False
    assert (await login(api, "a different password")).status_code == 401
    assert (await login(api)).status_code == 200

    admin = await founder(db)
    admin.is_active = False
    admin.password_hash = None
    await db.commit()
    assert await ensure_first_admin(db, redis, outbox) is False
    assert (await founder(db)).password_hash is None


@pytest.mark.parametrize("bad", ["short", EMAIL])
async def test_a_weak_password_is_not_used(db, redis, outbox, first_admin, bad, caplog) -> None:
    first_admin(bad)
    await no_admins(db)

    await ensure_first_admin(db, redis, outbox)

    admin = await founder(db)
    assert admin is not None and admin.password_hash is None  # falls back to the email link
    assert "FIRST_ADMIN_PASSWORD was not used" in caplog.text
    assert bad not in caplog.text or bad == EMAIL


def test_the_setting_must_be_an_email() -> None:
    with pytest.raises(ValueError, match="FIRST_ADMIN_EMAIL"):
        type(settings)(FIRST_ADMIN_EMAIL="not an email")
    assert type(settings)(FIRST_ADMIN_EMAIL="  Founder@Notchs.Test ").FIRST_ADMIN_EMAIL == EMAIL
