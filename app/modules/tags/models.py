"""The shared tags list, and how venues and visits use it.

    tags          one list, kept by admins only
    venue_tags    what a place is: vibe, cuisine, activity (at most 5, enforced in the service)
    venue_items   what you can try there, in menu order (at most 10, kind `item` only)
    visit_tags    what a customer said they tried. Earns no points: feeds trends and insights.

`venue_items` and `visit_tags` and the `item` kind are not in the spec sheet.
"""

import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, Entity, in_values
from app.modules.locations.models import SLUG_REGEX

MAX_NAME_LENGTH = 40


class TagKind(StrEnum):
    CUISINE = "cuisine"
    VIBE = "vibe"
    ACTIVITY = "activity"
    ITEM = "item"


class Tag(Entity):
    __tablename__ = "tags"
    __table_args__ = (
        CheckConstraint(in_values("kind", TagKind), name="kind_valid"),
        CheckConstraint(f"slug ~ '{SLUG_REGEX}'", name="slug_format"),
    )

    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH))
    slug: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), unique=True)
    kind: Mapped[str] = mapped_column(String(12), index=True)


class VenueTag(Base, CreatedAtMixin):
    __tablename__ = "venue_tags"

    venue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class VenueItem(Base, CreatedAtMixin):
    __tablename__ = "venue_items"
    __table_args__ = (
        UniqueConstraint("venue_id", "position", name="uq_venue_items_venue_position"),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    venue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger)


class VisitTag(Base, CreatedAtMixin):
    __tablename__ = "visit_tags"

    visit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("visits.id", ondelete="CASCADE"), primary_key=True
    )
    # RESTRICT: a tag customers have picked cannot be deleted, so past answers stay true.
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
