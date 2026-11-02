"""Customers. Their WhatsApp phone number is the identity; they have no password."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, InsertOnlyEntity, in_values, public_code_column

MAX_FOUNDING_MEMBER_NO = 500
# The privacy notice customers agree to in WhatsApp (B1): its "last updated" date (A10).
PRIVACY_NOTICE_VERSION = "2026-10-12"


class UserTheme(StrEnum):
    LIGHT = "light"
    DARK = "dark"


class UserStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    DELETED = "deleted"


class SignupSource(StrEnum):
    WHATSAPP = "whatsapp"  # their first WhatsApp message created them
    WEB = "web"  # the join form on notchs.com (A1 stop 5, A14)


class User(Entity):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            f"founding_member_no BETWEEN 1 AND {MAX_FOUNDING_MEMBER_NO}",
            name="founding_member_no_range",
        ),
        CheckConstraint(in_values("theme", UserTheme), name="theme_valid"),
        CheckConstraint(in_values("status", UserStatus), name="status_valid"),
        CheckConstraint(in_values("signup_source", SignupSource), name="signup_source_valid"),
    )

    # Public id for support and the admin panel (README, "IDs and public codes"). Added.
    user_code: Mapped[str] = public_code_column("USR")
    # Login identity, e.g. +919876543210. Never shown to venues.
    phone_e164: Mapped[str] = mapped_column(String(16), unique=True)
    # Asked the first time the passport opens. Shown on the Notch Card.
    name: Mapped[str | None] = mapped_column(String(60))
    # Only asked when the user goes public.
    handle: Mapped[str | None] = mapped_column(String(30), unique=True)
    # Home city, set from the first scanned venue.
    city_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cities.id", ondelete="SET NULL"), index=True
    )
    area_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("areas.id", ondelete="SET NULL"))
    # Private by default. Public = name on the leaderboard.
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # 18+ confirmed on the first scan.
    age_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Privacy consent (DPDP) and which notice version was agreed to.
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str | None] = mapped_column(String(20))
    # 1-500 only, given in join order.
    founding_member_no: Mapped[int | None] = mapped_column(Integer, unique=True)
    theme: Mapped[str] = mapped_column(
        String(10), default=UserTheme.LIGHT, server_default=UserTheme.LIGHT.value
    )
    status: Mapped[str] = mapped_column(
        String(12), default=UserStatus.ACTIVE, server_default=UserStatus.ACTIVE.value, index=True
    )
    # Last scan or app open.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set on a DELETE request; a job wipes the personal data.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Added: where the customer came from, and when they used the website's join form. The
    # form does not verify the number, so it never counts as WhatsApp consent (B1).
    signup_source: Mapped[str] = mapped_column(
        String(10), default=SignupSource.WHATSAPP, server_default=SignupSource.WHATSAPP.value
    )
    web_joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeletionSource(StrEnum):
    WHATSAPP = "whatsapp"  # DELETE, then YES (B9)
    APP = "app"  # "Delete everything" in the passport (C12)
    ADMIN = "admin"  # an admin handling a request (G3)


class DeletionStatus(StrEnum):
    AWAITING_YES = "awaiting_yes"  # sent DELETE on WhatsApp, not confirmed yet
    CONFIRMED = "confirmed"  # the account is closed; the wipe job removes the data next
    DONE = "done"  # personal data removed
    EXPIRED = "expired"  # never confirmed


class UserDeletion(Entity):
    """One deletion request, kept after the customer is gone so the team can show it was done
    (G3). Holds no personal data: the customer's public code and the last 4 digits only."""

    __tablename__ = "user_deletions"
    __table_args__ = (
        CheckConstraint(in_values("source", DeletionSource), name="source_valid"),
        CheckConstraint(in_values("status", DeletionStatus), name="status_valid"),
    )

    # No foreign key: the user row is deleted by the wipe.
    user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    user_code: Mapped[str] = mapped_column(String(12))
    phone_last4: Mapped[str] = mapped_column(String(4))
    source: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(14), index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )


class ConsentKind(StrEnum):
    # B1: "confirm you're 18+ and agree to our privacy notice", in WhatsApp.
    PRIVACY_NOTICE = "privacy_notice"


class UserConsent(InsertOnlyEntity):
    """Every consent a customer gave, with the exact words they were shown (DPDP). Rows are
    only added: a new version of the wording means a new row."""

    __tablename__ = "user_consents"
    __table_args__ = (
        CheckConstraint(in_values("kind", ConsentKind), name="kind_valid"),
        CheckConstraint(in_values("source", SignupSource), name="source_valid"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24))
    # The text exactly as shown, and how it was accepted ("I agree" button).
    wording: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(10))
