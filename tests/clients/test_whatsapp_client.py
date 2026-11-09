import json

import httpx
import pytest
from pydantic import SecretStr

from app.clients.whatsapp import (
    MAX_MEDIA_BYTES,
    WhatsAppClient,
    WhatsAppMediaError,
    WhatsAppSendError,
    download_media,
)
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


# --- bill photos (claims) -----------------------------------------------------------------------

MEDIA_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1"


def _media(info: dict | None = None, *, info_status: int = 200, file_status: int = 200):
    """Meta: the media's details at graph.facebook.com, then the file at its download link."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "graph.facebook.com":
            body = {"url": MEDIA_URL, "mime_type": "image/jpeg", "file_size": "4"} | (info or {})
            return httpx.Response(info_status, json=body)
        return httpx.Response(file_status, content=b"\xff\xd8ok")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


async def test_download_media(live: None) -> None:
    http, seen = _media()

    media = await download_media("1234567890", http)

    assert (media.content, media.mime_type) == (b"\xff\xd8ok", "image/jpeg")
    assert str(seen[0].url).endswith(f"/{settings.WHATSAPP_API_VERSION}/1234567890")
    assert [r.headers["Authorization"] for r in seen] == ["Bearer EAAtest"] * 2


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"info_status": 404}, "gone"),
        ({"file_status": 404}, "gone"),
        ({"file_status": 302}, "bad_response"),  # redirects are not followed
        ({"info": {"mime_type": "application/pdf"}}, "not_a_photo"),
        ({"info": {"file_size": str(MAX_MEDIA_BYTES + 1)}}, "too_large"),
        ({"info": {"url": "http://example.com/x"}}, "bad_response"),
    ],
)
async def test_download_media_refuses(live: None, kwargs: dict, code: str) -> None:
    http, _ = _media(**kwargs)

    with pytest.raises(WhatsAppMediaError) as error:
        await download_media("1234567890", http)

    assert error.value.code == code


async def test_download_media_needs_live_whatsapp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "WHATSAPP_DRY_RUN", True)

    with pytest.raises(WhatsAppMediaError) as error:
        await download_media("1234567890")

    assert error.value.code == "not_configured"
