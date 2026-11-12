"""`visit_feedback`: a 1 to 5 rating and an optional comment after a visit (later feature).

One per visit. Venues can read their feedback but never delete it; the team can hide abusive
comments (status `hidden`). Shown with the customer's handle only, never their phone number.
"""

import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, in_values


class FeedbackStatus(StrEnum):
    VISIBLE = "visible"
    HIDDEN = "hidden"  # moderated by the team


class VisitFeedback(Entity):
    __tablename__ = "visit_feedback"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),
        CheckConstraint(in_values("status", FeedbackStatus), name="status_valid"),
        Index("ix_visit_feedback_venue_created_at", "venue_id", "created_at"),
    )

    visit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("visits.id", ondelete="CASCADE"), unique=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(
        String(8), default=FeedbackStatus.VISIBLE, server_default=FeedbackStatus.VISIBLE.value
    )
