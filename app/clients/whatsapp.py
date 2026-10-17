"""Meta's WhatsApp Cloud API: sending one message.

With WHATSAPP_DRY_RUN=true (the default for local work) nothing goes to Meta: the message is
written to the log instead, and no Meta account is needed. Storing messages is not done here;
see app/modules/whatsapp/sender.py.
"""

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.common.phone import masked_phone
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class SendResult:
    # Meta's id for the message (wamid...). None in dry-run mode.
    wa_message_id: str | None
    dry_run: bool


class WhatsAppSendError(Exception):
    """Meta refused the message or could not be reached. `code` is Meta's error code, or
    "network" / "bad_response"."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"WhatsApp send failed ({code}): {message}")
        self.code = code


class WhatsAppClient:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        # Tests pass a client with a mock transport; otherwise one client per send.
        self._http = http

    def _messages_url(self) -> str:
        return (
            f"{settings.WHATSAPP_GRAPH_URL}/{settings.WHATSAPP_API_VERSION}/"
            f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )

    async def send(self, payload: dict[str, Any]) -> SendResult:
        if settings.WHATSAPP_DRY_RUN:
            # Logs never carry a customer's full phone number: only its last 4 digits.
            shown = payload | {"to": masked_phone(str(payload.get("to", "")))}
            logger.info("WHATSAPP (dry run) %s", json.dumps(shown, ensure_ascii=False))
            return SendResult(wa_message_id=None, dry_run=True)
        if settings.WHATSAPP_ACCESS_TOKEN is None or not settings.WHATSAPP_PHONE_NUMBER_ID:
            raise WhatsAppSendError("not_configured", "WhatsApp settings are missing")

        headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN.get_secret_value()}"}
        try:
            if self._http is not None:
                response = await self._http.post(
                    self._messages_url(), json=payload, headers=headers
                )
            else:
                async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
                    response = await http.post(self._messages_url(), json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise WhatsAppSendError("network", str(exc)) from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise WhatsAppSendError("bad_response", f"HTTP {response.status_code}") from exc

        if response.is_error or "error" in body:
            error = body.get("error", {})
            code = str(error.get("code", response.status_code))
            # Meta's message names the problem (expired token, number not allowed, ...).
            raise WhatsAppSendError(code, str(error.get("message", "unknown error")))

        try:
            return SendResult(wa_message_id=body["messages"][0]["id"], dry_run=False)
        except (KeyError, IndexError, TypeError) as exc:
            raise WhatsAppSendError("bad_response", "no message id in Meta's answer") from exc


def get_whatsapp_client() -> WhatsAppClient:
    """FastAPI dependency. Override it in tests."""
    return WhatsAppClient()
