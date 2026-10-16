"""Customers. Their WhatsApp phone number is the identity; they have no password."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, in_values

MAX_FOUNDING_MEMBER_NO = 500


class UserTheme(StrEnum):
    LIGHT = "light"
    DARK = "dark"


class UserStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    DELETED = "deleted"


class User(Entity):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            f"founding_member_no BETWEEN 1 AND {MAX_FOUNDING_MEMBER_NO}",
            name="founding_member_no_range",
        ),
        CheckConstraint(in_values("theme", UserTheme), name="theme_valid"),
        CheckConstraint(in_values("status", UserStatus), name="status_valid"),
    )

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
