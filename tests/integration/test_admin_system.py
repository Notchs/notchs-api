"""Admin Settings → System: on/off states (never secrets) and the test email."""

import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.clients.email import EmailSendError, get_email_client
from app.core.config import settings
from app.main import app
from app.modules.admin_panel.system import TEST_EMAILS_PER_WINDOW
from app.modules.admin_users.models import AdminRole
from tests.integration.test_admin import make_admin

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_needs_an_admin(api: AsyncClient, db, redis) -> None:
    api.cookies.clear()
    assert (await api.get("/admin/system")).status_code == 401
    assert (await api.post("/admin/system/test-email")).status_code == 401
    reviewer = await make_admin(db, redis, api, AdminRole.REVIEWER)
    assert (await api.get("/admin/system", cookies=reviewer)).status_code == 200
    assert (await api.post("/admin/system/test-email", cookies=reviewer)).status_code == 403


async def test_status_says_what_is_switched_on(api: AsyncClient, db, redis, monkeypatch) -> None:
    admin = await make_admin(db, redis, api)
    monkeypatch.setattr(settings, "EMAIL_DRY_RUN", True)
    monkeypatch.setattr(settings, "WHATSAPP_DRY_RUN", True)

    dry = (await api.get("/admin/system", cookies=admin)).json()["data"]

    monkeypatch.setattr(settings, "EMAIL_DRY_RUN", False)
    monkeypatch.setattr(settings, "SMTP_HOST", None)
    monkeypatch.setattr(settings, "RESEND_API_KEY", SecretStr("re_test_key_value"))
    monkeypatch.setattr(settings, "WHATSAPP_DRY_RUN", False)
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", SecretStr("EAAtest_token_value"))
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setattr(settings, "TURNSTILE_DISABLED", False)
    monkeypatch.setattr(settings, "TURNSTILE_SECRET", None)
    response = await api.get("/admin/system", cookies=admin)
    live = response.json()["data"]

    assert (dry["email"], dry["whatsapp"]) == ("dry_run", "dry_run")
    assert (live["email"], live["whatsapp"], live["bot_check"]) == (
        "sending",
        "sending",
        "not_configured",
    )
    assert live["admin_app_url"] == settings.ADMIN_APP_URL
    # States only: never a key or a token.
    assert "re_test_key_value" not in response.text
    assert "EAAtest_token_value" not in response.text


async def test_test_email_goes_to_me(api: AsyncClient, db, redis, outbox, monkeypatch) -> None:
    monkeypatch.setattr(settings, "EMAIL_DRY_RUN", False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", SecretStr("re_test_key_value"))
    admin = await make_admin(db, redis, api)
    me = (await api.get("/admin/me", cookies=admin)).json()["data"]["email"]
    sent_before = len(outbox.sent)

    response = await api.post("/admin/system/test-email", cookies=admin)

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"sent_to": me, "mode": "sending"}
    assert [m.to for m in outbox.sent[sent_before:]] == [me]


async def test_a_refused_test_email_says_why_and_is_limited(api: AsyncClient, db, redis) -> None:
    class Refusing:
        async def send(self, message) -> None:
            raise EmailSendError("Resend returned 403: The notchs.com domain is not verified.")

    admin = await make_admin(db, redis, api)
    app.dependency_overrides[get_email_client] = lambda: Refusing()

    first = await api.post("/admin/system/test-email", cookies=admin)
    for _ in range(TEST_EMAILS_PER_WINDOW - 1):
        await api.post("/admin/system/test-email", cookies=admin)
    limited = await api.post("/admin/system/test-email", cookies=admin)

    assert first.status_code == 502
    assert "domain is not verified" in first.json()["error"]["message"]
    assert limited.status_code == 429
