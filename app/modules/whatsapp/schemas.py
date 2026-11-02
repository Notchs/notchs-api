"""Request and response bodies for the WhatsApp dev tools."""

import uuid
from typing import Self

from pydantic import Field, model_validator

from app.common.phone import Phone
from app.common.schemas import BaseSchema

MAX_TEXT_LENGTH = 4096  # WhatsApp's limit for a text message
MAX_BUTTON_ID_LENGTH = 256  # WhatsApp's limit for a reply button id


class SimulateInboundIn(BaseSchema):
    """One of: a text the customer types, a tap on one of the bot's buttons (its id, e.g.
    `login` for the help reply's "Login link"), or a scan at a venue by its scan code, which
    sends the pre-typed message with that counter's live code."""

    phone: Phone = Field(examples=["+919876543210"])
    text: str | None = Field(
        default=None, min_length=1, max_length=MAX_TEXT_LENGTH, examples=["LOGIN"]
    )
    button: str | None = Field(
        default=None, min_length=1, max_length=MAX_BUTTON_ID_LENGTH, examples=[None]
    )

    scan: str | None = Field(default=None, min_length=1, max_length=10, examples=[None])

    @model_validator(mode="after")
    def _one_kind_of_message(self) -> Self:
        if sum(value is not None for value in (self.text, self.button, self.scan)) != 1:
            raise ValueError("Send exactly one of text, button or scan")
        return self


class SimulatedInboundOut(BaseSchema):
    message_id: uuid.UUID
    # The customer the bot found or created; None if the bot never got to it.
    customer_id: uuid.UUID | None
    # What the bot would have sent on WhatsApp, in order.
    replies: list[str]
