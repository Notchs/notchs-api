"""The first admin from FIRST_ADMIN_EMAIL: only while no admin exists, and emailed a link."""

import pytest
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.modules.admin_users.bootstrap import ensure_first_admin
from app.modules.admin_users.models import AdminRole, AdminUser
from tests.integration.test_admin import make_admin

pytestmark = pytest.mark.asyncio(loop_scope="session")

EMAIL = "founder@notchs.test"


async def no_admins(db) -> None:
    await db.execute(delete(AdminUser))
    await db.commit()


async def test_does_nothing_when_unset(db, redis, outbox, monkeypatch) -> None:
    monkeypatch.setattr(settings, "FIRST_ADMIN_EMAIL", None)
    await no_admins(db)

    assert await ensure_first_admin(db, redis, outbox) is False
    assert await db.scalar(select(func.count()).select_from(AdminUser)) == 0
    assert outbox.sent == []


async def test_creates_the_first_admin_once(db, redis, outbox, monkeypatch) -> None:
    monkeypatch.setattr(settings, "FIRST_ADMIN_EMAIL", EMAIL)
    monkeypatch.setattr(settings, "FIRST_ADMIN_NAME", "Harsh")
    await no_admins(db)

    created = await ensure_first_admin(db, redis, outbox)
    again = await ensure_first_admin(db, redis, outbox)

    assert (created, again) == (True, False)
    admins = (await db.scalars(select(AdminUser))).all()
    assert [(a.email, a.name, a.access) for a in admins] == [(EMAIL, "Harsh", AdminRole.ADMIN)]
    assert len(outbox.sent) == 1
    assert outbox.sent[0].to == EMAIL
    assert f"{settings.ADMIN_APP_URL}/set-password?token=" in outbox.sent[0].text


async def test_ignored_once_any_admin_exists(api, db, redis, outbox, monkeypatch) -> None:
    await make_admin(db, redis, api)
    sent_before = len(outbox.sent)
    monkeypatch.setattr(settings, "FIRST_ADMIN_EMAIL", EMAIL)

    assert await ensure_first_admin(db, redis, outbox) is False
    assert await db.scalar(select(AdminUser.id).where(AdminUser.email == EMAIL)) is None
    assert len(outbox.sent) == sent_before


def test_the_setting_must_be_an_email() -> None:
    with pytest.raises(ValueError, match="FIRST_ADMIN_EMAIL"):
        type(settings)(FIRST_ADMIN_EMAIL="not an email")
    assert type(settings)(FIRST_ADMIN_EMAIL="  Founder@Notchs.Test ").FIRST_ADMIN_EMAIL == EMAIL
