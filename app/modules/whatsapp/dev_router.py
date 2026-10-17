"""`/dev`: act as a WhatsApp customer without a phone or Meta. Never available in production.

`POST /dev/simulate-inbound` sends a text through the same path as a real message (parsed,
stored, handled by the bot) and returns the bot's replies, login links included. Nothing goes
to Meta, whatever WHATSAPP_DRY_RUN says. Admins (role `admin`) only, because a reply can hold
a working login link for the customer with that number.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.clients.whatsapp import SendResult
from app.common.dependencies import DBSession
from app.common.exceptions import NotFoundException
from app.common.phone import masked_phone
from app.common.schemas import CustomResponse
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.auth.dependencies import CurrentFullAdmin
from app.modules.whatsapp.bot import WhatsAppBot
from app.modules.whatsapp.models import WhatsAppMessage
from app.modules.whatsapp.processor import (
    SIMULATED_ID_PREFIX,
    InboundProcessor,
    get_inbound_processor,
)
from app.modules.whatsapp.schemas import SimulatedInboundOut, SimulateInboundIn
from app.modules.whatsapp.service import WhatsAppWebhookService
from app.modules.whatsapp.webhook import parse_webhook

logger = get_logger(__name__)


def not_in_production() -> None:
    if settings.is_production:
        raise NotFoundException()


router = APIRouter(
    prefix="/dev",
    tags=["Dev tools"],
    dependencies=[Depends(not_in_production)],
    include_in_schema=not settings.is_production,
)

ProcessorDep = Annotated[InboundProcessor, Depends(get_inbound_processor)]


class CapturingClient:
    """Stands in for WhatsAppClient: keeps the replies instead of sending them."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, payload: dict[str, Any]) -> SendResult:
        self.sent.append(payload)
        return SendResult(wa_message_id=None, dry_run=True)

    def texts(self) -> list[str]:
        return [_text_of(payload) for payload in self.sent]


def _text_of(payload: dict[str, Any]) -> str:
    if payload["type"] == "interactive":  # buttons: the text above them
        return payload["interactive"]["body"]["text"]
    return payload["text"]["body"]


def meta_payload(phone_e164: str, text: str) -> dict[str, Any]:
    """What Meta's webhook would deliver for this text."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "SIMULATED",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": phone_e164.removeprefix("+"),
                                    "id": f"{SIMULATED_ID_PREFIX}{uuid.uuid4()}",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


@router.post(
    "/simulate-inbound",
    response_model=CustomResponse[SimulatedInboundOut],
    summary="Send a WhatsApp text as a customer and get the bot's replies (not in production)",
)
async def simulate_inbound(
    body: SimulateInboundIn,
    admin: CurrentFullAdmin,
    session: DBSession,
    processor: ProcessorDep,
) -> CustomResponse[SimulatedInboundOut]:
    stored = await WhatsAppWebhookService(session).store(
        parse_webhook(meta_payload(body.phone, body.text))
    )
    await session.commit()
    (message_id,) = stored.new_message_ids

    capture = CapturingClient()
    bot = WhatsAppBot(processor.redis, capture)  # type: ignore[arg-type]
    await InboundProcessor(processor.session_factory, processor.redis, bot).process(message_id)

    customer_id = await session.scalar(
        select(WhatsAppMessage.user_id).where(WhatsAppMessage.id == message_id)
    )
    logger.info("Admin %s simulated a WhatsApp message from %s", admin.id, masked_phone(body.phone))
    return CustomResponse(
        message="Simulated",
        data=SimulatedInboundOut(
            message_id=message_id, customer_id=customer_id, replies=capture.texts()
        ),
    )
