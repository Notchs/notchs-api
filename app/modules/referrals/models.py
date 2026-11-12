"""Referrals and Star Points (team decision Q33: 50 Star Points per person referred).

    referrals           who invited whom; one inviter per customer, set in their first 14 days
    star_points_ledger  every Star Point given, with the reason; never edited

Star Points are separate from the points that decide rank. What they can be spent on is not
decided yet, so nothing spends them.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, InsertOnlyEntity, in_values


class ReferralStatus(StrEnum):
    PENDING = "pending"  # the invited customer has no real Notch yet
    REWARDED = "rewarded"  # the inviter got the Star Points
    CAPPED = "capped"  # would have been rewarded, but the inviter was past the monthly cap
    CANCELLED = "cancelled"  # the Notch that triggered it was removed (Star Points taken back)


class ReferralSource(StrEnum):
    LINK = "link"  # notchs.com/?ref=<handle>
    TYPED_HANDLE = "typed_handle"


class Referral(Entity):
    __tablename__ = "referrals"
    __table_args__ = (
        CheckConstraint("referrer_id <> referred_id", name="not_self"),
        CheckConstraint(in_values("status", ReferralStatus), name="status_valid"),
        CheckConstraint(in_values("source", ReferralSource), name="source_valid"),
        CheckConstraint(
            "(status = 'pending') = (rewarded_at IS NULL)", name="rewarded_at_unless_pending"
        ),
        Index("ix_referrals_referrer_id_rewarded_at", "referrer_id", "rewarded_at"),
    )

    referrer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Each customer is referred at most once.
    referred_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    # Set when the referred customer got their first real Notch and the inviter was rewarded
    # (or would have been, past the monthly cap: status `capped`).
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(10), default=ReferralStatus.PENDING, server_default=ReferralStatus.PENDING.value
    )
    # The Notch that triggered the reward. If it is removed as fraud, the reward is taken back.
    qualifying_visit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("visits.id", ondelete="SET NULL")
    )
    source: Mapped[str | None] = mapped_column(String(14))


class StarPointsReason(StrEnum):
    REFERRAL = "referral"
    # The referral's qualifying Notch was removed: the 50 come back off (negative).
    REFERRAL_REVERSED = "referral_reversed"
    ADJUSTMENT = "adjustment"  # an admin's correction (may be negative)


class StarPointsEntry(InsertOnlyEntity):
    __tablename__ = "star_points_ledger"
    __table_args__ = (
        CheckConstraint(in_values("reason", StarPointsReason), name="reason_valid"),
        Index("ix_star_points_ledger_user_id_created_at", "user_id", "created_at"),
        # One reward, and at most one reversal, per referral.
        Index(
            "uq_star_points_ledger_referral_reason",
            "referral_id",
            "reason",
            unique=True,
            postgresql_where=text("referral_id IS NOT NULL"),
        ),
        CheckConstraint("reason <> 'referral' OR points > 0", name="referral_positive"),
        CheckConstraint("reason <> 'referral_reversed' OR points < 0", name="reversal_negative"),
        CheckConstraint("(reason = 'adjustment') = (note IS NOT NULL)", name="note_on_adjustments"),
        CheckConstraint(
            "reason = 'adjustment' OR admin_user_id IS NULL", name="admin_only_on_adjustments"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    referral_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("referrals.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(String(20))
    points: Mapped[int] = mapped_column(Integer)
    # Adjustments: who made it and why.
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(String(255))
