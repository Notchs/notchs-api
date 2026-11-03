import json
import smtplib
from typing import ClassVar

import httpx
import pytest
from pydantic import SecretStr

from app.clients.email import EmailClient, EmailMessage, EmailSendError
from app.clients.turnstile import TurnstileClient
from app.core.config import settings

MESSAGE = EmailMessage(to="owner@kahwa.in", subject="Your code", text="Your code is 123456")


def _http(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


@pytest.fixture
def live_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMAIL_DRY_RUN", False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", SecretStr("re_test_key"))


async def test_dry_run_sends_nothing(caplog: pytest.LogCaptureFixture) -> None:
    def fail(_: httpx.Request) -> httpx.Response:
        raise AssertionError("dry run must not call Resend")

    caplog.set_level("INFO")
    await EmailClient(_http(httpx.MockTransport(fail))).send(MESSAGE)

    assert "123456" in caplog.text


class FakeSmtp:
    """Stands in for smtplib.SMTP: keeps what would have been sent."""

    sent: ClassVar[list] = []
    connected_to: tuple = ()

    def __init__(self, host: str, port: int, timeout: float) -> None:
        FakeSmtp.connected_to = (host, port)

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def send_message(self, message) -> None:
        FakeSmtp.sent.append(message)


@pytest.fixture
def smtp_email(monkeypatch: pytest.MonkeyPatch) -> type[FakeSmtp]:
    monkeypatch.setattr(settings, "EMAIL_DRY_RUN", False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", None)
    monkeypatch.setattr(settings, "SMTP_HOST", "mailpit")
    monkeypatch.setattr(settings, "SMTP_PORT", 1025)
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    FakeSmtp.sent = []
    return FakeSmtp


async def test_smtp_sends_there_and_not_to_resend(smtp_email: type[FakeSmtp]) -> None:
    def fail(_: httpx.Request) -> httpx.Response:
        raise AssertionError("SMTP must not call Resend")

    await EmailClient(_http(httpx.MockTransport(fail))).send(MESSAGE)

    assert smtp_email.connected_to == ("mailpit", 1025)
    (sent,) = smtp_email.sent
    assert (sent["To"], sent["Subject"]) == (MESSAGE.to, MESSAGE.subject)
    assert sent["From"] == settings.EMAIL_FROM
    assert "123456" in sent.get_content()


async def test_smtp_keeps_the_html_part(smtp_email: type[FakeSmtp]) -> None:
    html = EmailMessage(to="owner@kahwa.in", subject="Hi", text="Plain", html="<b>Rich</b>")

    await EmailClient().send(html)

    (sent,) = smtp_email.sent
    assert "Rich" in sent.get_body(("html",)).get_content()
    assert "Plain" in sent.get_body(("plain",)).get_content()


@pytest.mark.parametrize("error", [OSError("down"), smtplib.SMTPException("refused")])
async def test_smtp_failures_are_send_errors(
    smtp_email: type[FakeSmtp], monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def fails(*_: object) -> None:
        raise error

    monkeypatch.setattr(FakeSmtp, "send_message", fails)

    with pytest.raises(EmailSendError, match="SMTP"):
        await EmailClient().send(MESSAGE)


async def test_sent_emails_never_log_the_address_or_the_code(
    smtp_email: type[FakeSmtp], live_email: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")

    await EmailClient().send(MESSAGE)

    assert smtp_email.sent
    assert MESSAGE.to not in caplog.text
    assert "123456" not in caplog.text


async def test_dry_run_wins_over_smtp(
    smtp_email: type[FakeSmtp], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "EMAIL_DRY_RUN", True)

    await EmailClient().send(MESSAGE)

    assert smtp_email.sent == []


async def test_sends_through_resend(live_email: None) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "email_1"})

    await EmailClient(_http(httpx.MockTransport(handler))).send(MESSAGE)

    body = json.loads(seen[0].content)
    assert seen[0].headers["Authorization"] == "Bearer re_test_key"
    assert body == {
        "from": settings.EMAIL_FROM,
        "to": ["owner@kahwa.in"],
        "subject": "Your code",
        "text": "Your code is 123456",
    }


async def test_resend_error_is_raised(live_email: None) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(403, json={"message": "bad key"}))

    with pytest.raises(EmailSendError, match="403"):
        await EmailClient(_http(transport)).send(MESSAGE)


async def test_network_error_is_raised(live_email: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(EmailSendError):
        await EmailClient(_http(httpx.MockTransport(handler))).send(MESSAGE)


@pytest.fixture
def turnstile_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TURNSTILE_DISABLED", False)
    monkeypatch.setattr(settings, "TURNSTILE_SECRET", SecretStr("ts_secret"))


async def test_turnstile_passes_token_secret_and_ip(turnstile_on: None) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"success": True})

    assert await TurnstileClient(_http(httpx.MockTransport(handler))).verify("tok", ip="1.2.3.4")
    assert seen[0].content == b"secret=ts_secret&response=tok&remoteip=1.2.3.4"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"success": False, "error-codes": ["invalid-input-response"]}),
        httpx.Response(500),
        httpx.Response(200, text="not json"),
    ],
)
async def test_turnstile_fails_closed(turnstile_on: None, response: httpx.Response) -> None:
    client = TurnstileClient(_http(httpx.MockTransport(lambda _: response)))

    assert not await client.verify("tok")


@pytest.mark.parametrize("token", ["", "x" * 2049])
async def test_turnstile_rejects_bad_tokens_without_calling_out(
    turnstile_on: None, token: str
) -> None:
    def fail(_: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    assert not await TurnstileClient(_http(httpx.MockTransport(fail))).verify(token)


async def test_turnstile_without_secret_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TURNSTILE_DISABLED", False)
    monkeypatch.setattr(settings, "TURNSTILE_SECRET", None)

    assert not await TurnstileClient().verify("tok")


async def test_turnstile_can_be_disabled_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TURNSTILE_DISABLED", True)

    assert await TurnstileClient().verify("")
