"""`consents`: every consent someone gave, with the exact words they were shown (DPDP, CLAUDE.md
"Privacy"). Rows are never edited, except to set `withdrawn_at`.

    customers           the privacy notice and 18+ on WhatsApp (users/consent.py)
    venue applicants    the application form's notice (once the team approves its wording)

`users.consent_at` / `consent_version` stay as the quick "latest privacy-notice consent" and are
written in the same transaction. Deleting the application deletes its rows; deleting a
customer's account deletes theirs (users/deletion.py, the account row itself stays).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import InsertOnlyEntity, in_values


class ConsentSubject(StrEnum):
    USER = "user"
    VENUE_APPLICATION = "venue_application"


class ConsentPurpose(StrEnum):
    PRIVACY_NOTICE = "privacy_notice"  # customers: the notice and 18+
    VENUE_APPLICATION_PRIVACY = "venue_application_privacy"


class ConsentChannel(StrEnum):
    WHATSAPP = "whatsapp"
    WEBSITE = "website"


class Consent(InsertOnlyEntity):
    __tablename__ = "consents"
    __table_args__ = (
        CheckConstraint(in_values("subject_type", ConsentSubject), name="subject_type_valid"),
        CheckConstraint(in_values("purpose", ConsentPurpose), name="purpose_valid"),
        CheckConstraint(in_values("channel", ConsentChannel), name="channel_valid"),
        CheckConstraint(
            "((subject_type = 'user') = (user_id IS NOT NULL)) AND "
            "((subject_type = 'venue_application') = (venue_application_id IS NOT NULL))",
            name="subject_matches_type",
        ),
        CheckConstraint("wording_sha256 ~ '^[0-9a-f]{64}$'", name="wording_sha256_format"),
        Index("ix_consents_user_purpose_accepted_at", "user_id", "purpose", "accepted_at"),
        Index("ix_consents_venue_application_id", "venue_application_id"),
    )

    subject_type: Mapped[str] = mapped_column(String(20))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    venue_application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("venue_applications.id", ondelete="CASCADE")
    )
    purpose: Mapped[str] = mapped_column(String(30))
    version: Mapped[str] = mapped_column(String(20))
    # The exact words shown, and their SHA-256 (hex) for quick comparison.
    wording: Mapped[str] = mapped_column(Text)
    wording_sha256: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(10))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
