"""Admin Settings: adding admins, changing roles, turning them off, and never locking the panel."""

import uuid

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.modules.admin_users.models import AdminRole
from tests.integration.test_admin import ADMIN_PASSWORD, make_admin, token_from

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def code_of(api: AsyncClient, cookies) -> str:
    return (await api.get("/admin/me", cookies=cookies)).json()["data"]["admin_user_code"]


def new_email() -> str:
    return f"new-{uuid.uuid4().hex[:6]}@notchs.com"


async def test_admins_only(api: AsyncClient, db, redis) -> None:
    api.cookies.clear()
    assert (await api.get("/admin/users")).status_code == 401
    reviewer = await make_admin(db, redis, api, AdminRole.REVIEWER)

    assert (await api.get("/admin/users", cookies=reviewer)).status_code == 403
    invite = {"name": "Riya", "email": new_email()}
    assert (await api.post("/admin/users", json=invite, cookies=reviewer)).status_code == 403


async def test_invite_then_they_set_a_password(api: AsyncClient, db, redis, outbox) -> None:
    admin = await make_admin(db, redis, api)
    email = new_email()

    response = await api.post(
        "/admin/users", json={"name": "Riya", "email": email.upper()}, cookies=admin
    )
    again = await api.post("/admin/users", json={"name": "Riya", "email": email}, cookies=admin)

    assert response.status_code == 200, response.text
    added = response.json()["data"]
    assert (added["email"], added["role"], added["has_password"]) == (email, "reviewer", False)
    assert again.status_code == 409
    listed = (await api.get("/admin/users", cookies=admin)).json()["data"]
    assert email in [a["email"] for a in listed]
    token = token_from(outbox.sent[-1].text, settings.ADMIN_APP_URL)
    set_password = await api.post(
        "/admin/auth/set-password", json={"token": token, "password": ADMIN_PASSWORD}
    )
    assert set_password.status_code == 200, set_password.text


async def test_role_change_and_turning_off_logs_them_out(api: AsyncClient, db, redis) -> None:
    admin = await make_admin(db, redis, api)
    other = await make_admin(db, redis, api)
    other_code = await code_of(api, other)

    demoted = await api.patch(
        f"/admin/users/{other_code}", json={"role": "reviewer"}, cookies=admin
    )
    assert demoted.status_code == 200, demoted.text
    assert demoted.json()["data"]["role"] == "reviewer"
    assert (await api.get("/admin/users", cookies=other)).status_code == 403

    off = await api.patch(f"/admin/users/{other_code}", json={"is_active": False}, cookies=admin)
    assert off.json()["data"]["is_active"] is False
    assert (await api.get("/admin/me", cookies=other)).status_code in (401, 403)
    link = await api.post(f"/admin/users/{other_code}/link", cookies=admin)
    assert link.status_code == 409  # turn them back on first

    on = await api.patch(f"/admin/users/{other_code}", json={"is_active": True}, cookies=admin)
    assert on.json()["data"]["is_active"] is True
    assert (await api.post(f"/admin/users/{other_code}/link", cookies=admin)).status_code == 200


async def test_nobody_removes_their_own_access(api: AsyncClient, db, redis) -> None:
    admin = await make_admin(db, redis, api)
    me = await code_of(api, admin)

    demote = await api.patch(f"/admin/users/{me}", json={"role": "reviewer"}, cookies=admin)
    off = await api.patch(f"/admin/users/{me}", json={"is_active": False}, cookies=admin)
    rename_only = await api.patch(f"/admin/users/{me}", json={"role": "admin"}, cookies=admin)

    assert demote.status_code == off.status_code == 409
    assert demote.json()["error"]["code"] == "admin_users.self_change"
    assert rename_only.status_code == 200


async def test_unknown_admin(api: AsyncClient, db, redis) -> None:
    admin = await make_admin(db, redis, api)

    missing = await api.patch("/admin/users/ADM-7K3M9QX2", json={"role": "admin"}, cookies=admin)

    assert missing.status_code == 404
