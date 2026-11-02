import json

import httpx
import pytest
from pydantic import SecretStr

from app.clients.whatsapp import WhatsAppClient, WhatsAppSendError
from app.core.config import settings
from app.modules.whatsapp.payloads import text_message

PAYLOAD = text_message("+919876543210", "hello")


def _client(handler) -> WhatsAppClient:
    return WhatsAppClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "WHATSAPP_DRY_RUN", False)
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", SecretStr("EAAtest"))


async def test_dry_run_sends_nothing_and_masks_the_number(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "WHATSAPP_DRY_RUN", True)

    def fail(_: httpx.Request) -> httpx.Response:
        raise AssertionError("dry run must not call Meta")

    caplog.set_level("INFO")
    result = await _client(fail).send(PAYLOAD)

    assert (result.wa_message_id, result.dry_run) == (None, True)
    assert "hello" in caplog.text and "3210" in caplog.text
    assert "919876543210" not in caplog.text


async def test_sends_to_meta(live: None) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})

    result = await _client(handler).send(PAYLOAD)

    assert (result.wa_message_id, result.dry_run) == ("wamid.ABC", False)
    request = seen[0]
    expected_url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/1234567890/messages"
    assert str(request.url) == expected_url
    assert request.headers["Authorization"] == "Bearer EAAtest"
    assert json.loads(request.content) == PAYLOAD


async def test_meta_error_is_raised_with_its_code(live: None) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": "Error validating access token", "code": 190}}
        )

    with pytest.raises(WhatsAppSendError) as caught:
        await _client(handler).send(PAYLOAD)

    assert caught.value.code == "190"
    assert "access token" in str(caught.value)


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(200, text="not json"), "bad_response"),
        (httpx.Response(200, json={"messages": []}), "bad_response"),
        (httpx.Response(500, json={}), "500"),
    ],
    ids=["not-json", "no-id", "server-error"],
)
async def test_unusable_answers(live: None, response: httpx.Response, code: str) -> None:
    with pytest.raises(WhatsAppSendError) as caught:
        await _client(lambda _: response).send(PAYLOAD)

    assert caught.value.code == code


async def test_network_error(live: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(WhatsAppSendError) as caught:
        await _client(handler).send(PAYLOAD)

    assert caught.value.code == "network"
