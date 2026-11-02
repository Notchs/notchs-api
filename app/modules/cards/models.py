"""Monthly Notch Cards: the customer's card (C9, C10, A12) and the venue's card (E7).

Both are made by the monthly job on the 1st for the month that just ended, and kept as they
were made: a card is a snapshot, so later rejections or deletions never change a shared card.
The share images (1080x1920 story, 1080x1350 feed) are drawn by the app from these numbers.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, in_values


class CardFace(StrEnum):
    """Card Faces A, B, C (Page 3): one Volt hero number each."""

    COUNT = "A"  # "23 Notchs in October"
    PLACE = "B"  # "9 visits to Kahwa House"
    RANK = "C"  # "Top 8% of Ahmedabad"


class NotchCard(Entity):
    __tablename__ = "notch_cards"
    __table_args__ = (
        UniqueConstraint("user_id", "period", name="uq_notch_cards_user_id_period"),
        UniqueConstraint("period", "card_no", name="uq_notch_cards_period_card_no"),
        CheckConstraint(in_values("face", CardFace), name="face_valid"),
        CheckConstraint("extract(day from period) = 1", name="period_first_of_month"),
        CheckConstraint("rank_percent BETWEEN 1 AND 100", name="rank_percent_range"),
    )

    # Printed on the card and in its link: NC-1026-0142 (month, year, number that month).
    card_code: Mapped[str] = mapped_column(String(16), unique=True)
    card_no: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"))
    # The month, as its first day.
    period: Mapped[date] = mapped_column(Date)
    # The name to print, as it was when the card was made.
    name: Mapped[str | None] = mapped_column(String(60))
    notch_count: Mapped[int] = mapped_column(Integer)
    places_count: Mapped[int] = mapped_column(Integer)
    top_venue_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("venues.id", ondelete="SET NULL")
    )
    top_venue_name: Mapped[str | None] = mapped_column(String(120))
    top_venue_visits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The day they made their 5th visit to the top place that month ("Regular since 18 Oct").
    regular_since: Mapped[date | None] = mapped_column(Date)
    # "Late-night café hopper", and the average Notch time it came from ("22:40").
    vibe: Mapped[str | None] = mapped_column(String(60))
    avg_time: Mapped[str | None] = mapped_column(String(5))
    # "#38 in Ahmedabad" and "Top 8%". Only from 3 Notchs; the % only with 200+ active users.
    city_rank: Mapped[int | None] = mapped_column(Integer)
    rank_percent: Mapped[int | None] = mapped_column(SmallInteger)
    active_users: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # "Unnotched: 203 places in Ahmedabad": live places they have never notched.
    unnotched_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Fewer than 3 Notchs: a simpler card, no rank and no reveal (C9 state).
    is_simple: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # The face shown when shared; starts as the best one, "Change look" picks another.
    face: Mapped[str] = mapped_column(
        String(1), default=CardFace.COUNT, server_default=CardFace.COUNT.value
    )
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NULL = private. Shared cards open at /card/<card_code> for anyone (A12).
    shared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VenueCard(Entity):
    """The venue's monthly card (E7): "Kahwa House's October: 312 visitors, 41% new"."""

    __tablename__ = "venue_cards"
    __table_args__ = (
        UniqueConstraint("venue_id", "period", name="uq_venue_cards_venue_id_period"),
        CheckConstraint("extract(day from period) = 1", name="period_first_of_month"),
    )

    venue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venues.id", ondelete="CASCADE"), index=True
    )
    period: Mapped[date] = mapped_column(Date)
    # Different customers, and how many of them notched here for the first time ever.
    visitors: Mapped[int] = mapped_column(Integer)
    new_visitors: Mapped[int] = mapped_column(Integer)
    notches: Mapped[int] = mapped_column(Integer)
    # When the owner was emailed "Your Notch Card is ready".
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
