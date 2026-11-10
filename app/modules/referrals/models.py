"""Referrals and Star Points (team decision Q33: 50 Star Points per person referred).

    referrals           who invited whom; one inviter per customer, set in their first 14 days
    star_points_ledger  every Star Point given, with the reason; never edited

Star Points are separate from the points that decide rank. What they can be spent on is not
decided yet, so nothing spends them.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, InsertOnlyEntity, in_values


class Referral(Entity):
    __tablename__ = "referrals"
    __table_args__ = (
        CheckConstraint("referrer_id <> referred_id", name="not_self"),
        Index("ix_referrals_referrer_id_rewarded_at", "referrer_id", "rewarded_at"),
    )

    referrer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Each customer is referred at most once.
    referred_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    # Set when the referred customer got their first real Notch and the inviter was rewarded
    # (or would have been, past the monthly cap: see `capped`).
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StarPointsReason(StrEnum):
    REFERRAL = "referral"
    ADJUSTMENT = "adjustment"  # an admin's correction (may be negative)


class StarPointsEntry(InsertOnlyEntity):
    __tablename__ = "star_points_ledger"
    __table_args__ = (
        CheckConstraint(in_values("reason", StarPointsReason), name="reason_valid"),
        Index("ix_star_points_ledger_user_id_created_at", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # One reward per referral.
    referral_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("referrals.id", ondelete="SET NULL"), unique=True
    )
    reason: Mapped[str] = mapped_column(String(20))
    points: Mapped[int] = mapped_column(Integer)
