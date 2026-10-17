"""`/webhooks/whatsapp`: where Meta delivers incoming WhatsApp messages and delivery statuses.

Configure it in Meta (WhatsApp -> Configuration -> Webhook):
    Callback URL:  https://<api host>/api/v1/webhooks/whatsapp
    Verify token:  the value of WHATSAPP_VERIFY_TOKEN
    Fields:        messages
"""

import hmac
import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.common.dependencies import DBSession
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.whatsapp.processor import InboundProcessor, get_inbound_processor
from app.modules.whatsapp.service import WhatsAppWebhookService
from app.modules.whatsapp.webhook import parse_webhook, signature_is_valid

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["WhatsApp webhook"])

# Meta's payloads are a few KB; anything far bigger is not Meta.
MAX_BODY_BYTES = 1_000_000


@router.get("", response_class=PlainTextResponse, summary="Meta's one-time verification")
async def verify(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
) -> Response:
    expected = settings.WHATSAPP_VERIFY_TOKEN
    if (
        mode == "subscribe"
        and expected is not None
        and hmac.compare_digest(token, expected.get_secret_value())
    ):
        return PlainTextResponse(challenge)
    logger.warning("WhatsApp webhook verification refused")
    return PlainTextResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)


ProcessorDep = Annotated[InboundProcessor, Depends(get_inbound_processor)]


@router.post("", summary="Incoming messages and statuses from Meta")
async def receive(
    request: Request, session: DBSession, background: BackgroundTasks, processor: ProcessorDep
) -> Response:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return Response(status_code=status.HTTP_413_CONTENT_TOO_LARGE)

    secret = settings.WHATSAPP_APP_SECRET
    if secret is None:
        logger.error("WHATSAPP_APP_SECRET is not set; refusing every WhatsApp webhook")
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    if not signature_is_valid(
        raw, request.headers.get("x-hub-signature-256"), secret.get_secret_value()
    ):
        logger.warning("Refused a WhatsApp webhook with a missing or wrong signature")
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        payload = json.loads(raw)
    except ValueError:
        # Signed by Meta but unreadable: a 200 stops Meta retrying something that won't change.
        logger.warning("Signed WhatsApp webhook with a body that is not JSON")
        return Response(status_code=status.HTTP_200_OK)

    stored = await WhatsAppWebhookService(session).store(parse_webhook(payload))
    # Commit before handing over: the processor uses its own session and must see the rows.
    await session.commit()
    if stored.new_message_ids:
        # Runs after Meta has its 200. If the API stops first, the sweep picks them up.
        background.add_task(processor.process_many, stored.new_message_ids)
    if stored.new_message_ids or stored.statuses_applied:
        logger.info(
            "WhatsApp webhook: %d new message(s), %d status update(s)",
            len(stored.new_message_ids),
            stored.statuses_applied,
        )
    return Response(status_code=status.HTTP_200_OK)
