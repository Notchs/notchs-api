"""Request and response bodies for the WhatsApp dev tools."""

import uuid

from pydantic import Field

from app.common.phone import Phone
from app.common.schemas import BaseSchema

MAX_TEXT_LENGTH = 4096  # WhatsApp's limit for a text message


class SimulateInboundIn(BaseSchema):
    phone: Phone = Field(examples=["+919876543210"])
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH, examples=["LOGIN"])


class SimulatedInboundOut(BaseSchema):
    message_id: uuid.UUID
    # The customer the bot found or created; None if the bot never got to it.
    customer_id: uuid.UUID | None
    # What the bot would have sent on WhatsApp, in order.
    replies: list[str]
