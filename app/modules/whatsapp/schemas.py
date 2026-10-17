"""Request and response bodies for the WhatsApp dev tools."""

import uuid
from typing import Self

from pydantic import Field, model_validator

from app.common.phone import Phone
from app.common.schemas import BaseSchema

MAX_TEXT_LENGTH = 4096  # WhatsApp's limit for a text message
MAX_BUTTON_ID_LENGTH = 256  # WhatsApp's limit for a reply button id


class SimulateInboundIn(BaseSchema):
    """Either a text the customer types, or a tap on one of the bot's buttons (its id, e.g.
    `login` for the help reply's "Login link")."""

    phone: Phone = Field(examples=["+919876543210"])
    text: str | None = Field(
        default=None, min_length=1, max_length=MAX_TEXT_LENGTH, examples=["LOGIN"]
    )
    button: str | None = Field(
        default=None, min_length=1, max_length=MAX_BUTTON_ID_LENGTH, examples=[None]
    )

    @model_validator(mode="after")
    def _text_or_button(self) -> Self:
        if (self.text is None) == (self.button is None):
            raise ValueError("Send either text or button, not both")
        return self


class SimulatedInboundOut(BaseSchema):
    message_id: uuid.UUID
    # The customer the bot found or created; None if the bot never got to it.
    customer_id: uuid.UUID | None
    # What the bot would have sent on WhatsApp, in order.
    replies: list[str]
