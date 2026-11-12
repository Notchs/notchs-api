"""Every WhatsApp message in and out. Meta's message id makes a redelivered webhook harmless."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, in_values


class MessageDirection(StrEnum):
    IN = "in"
    OUT = "out"


class MessageType(StrEnum):
    TEXT = "text"
    BUTTON = "button"  # an inbound button tap, or an outbound message with buttons
    IMAGE = "image"
    TEMPLATE = "template"  # paid, business-initiated messages; not used in the MVP
    # Added: voice notes, stickers, locations, reactions... stored with their raw data.
    OTHER = "other"


class MessageStatus(StrEnum):
    RECEIVED = "received"  # inbound
    SENT = "sent"  # outbound, accepted by Meta (or logged in dry-run mode)
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class WhatsAppMessage(Entity):
    """From the table sheet, plus `processed_at` (added): when the bot finished handling an
    inbound message, so a restart never handles a message twice or skips one."""

    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        CheckConstraint(in_values("direction", MessageDirection), name="direction_valid"),
        CheckConstraint(in_values("message_type", MessageType), name="message_type_valid"),
        CheckConstraint(in_values("status", MessageStatus), name="status_valid"),
        # Paid business-initiated templates are not allowed (CLAUDE.md). Lifting this needs the
        # team's agreement and its own migration.
        CheckConstraint(
            "message_type <> 'template' AND template_name IS NULL", name="no_templates"
        ),
        # Inbound messages still waiting for the bot: what the retry sweep looks for.
        Index(
            "ix_whatsapp_messages_unprocessed",
            "created_at",
            postgresql_where=text("direction = 'in' AND processed_at IS NULL"),
        ),
    )

    # Meta's id (wamid...). Unique: a webhook Meta delivers twice is stored once. Outbound
    # messages Meta never accepted (dry run, failures) get a local id ("local:<uuid>").
    wa_message_id: Mapped[str] = mapped_column(String(128), unique=True)
    direction: Mapped[str] = mapped_column(String(4))
    # None once the customer deleted their account (users/deletion.py).
    phone_e164: Mapped[str | None] = mapped_column(String(16), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    message_type: Mapped[str] = mapped_column(String(12))
    # For paid templates (e.g. "card ready"). Not used in the MVP.
    template_name: Mapped[str | None] = mapped_column(String(60))
    body: Mapped[str | None] = mapped_column(Text)
    # The raw webhook data (inbound) or the request sent to Meta (outbound).
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12))
    error_code: Mapped[str | None] = mapped_column(String(20))
    # Added: when the bot finished handling this inbound message. NULL = still to do.
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Outbound: the customer message this replies to (proof every reply is inside the 24-hour
    # window).
    in_reply_to_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_messages.id", ondelete="SET NULL")
    )
    # Meta's short error title next to `error_code`. Never message text.
    error_title: Mapped[str | None] = mapped_column(String(255))
    # From Meta's status webhooks.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
