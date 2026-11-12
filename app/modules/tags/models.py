"""The shared tags list, and how venues and visits use it.

    tags          one list, kept by admins only
    venue_tags    what a place is: vibe, cuisine, activity (at most 5, enforced in the service)
    venue_items   what you can try there, in menu order (at most 10, kind `item` only)
    visit_tags    what a customer said they tried. Earns no points: feeds trends and insights.

`venue_items` and `visit_tags` and the `item` kind are not in the spec sheet.
"""

import uuid
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, Entity, TimestampMixin, in_values
from app.modules.locations.models import SLUG_REGEX
from app.modules.venues.models import VenueCategory

MAX_NAME_LENGTH = 40


class TagKind(StrEnum):
    CUISINE = "cuisine"
    VIBE = "vibe"
    ACTIVITY = "activity"
    ITEM = "item"


class TagStatus(StrEnum):
    ACTIVE = "active"
    HIDDEN = "hidden"  # out of pickers; still shown on old venues and visits
    SUGGESTED = "suggested"  # asked for by a venue; only admins see it until made active


class Tag(Entity):
    __tablename__ = "tags"
    __table_args__ = (
        CheckConstraint(in_values("kind", TagKind), name="kind_valid"),
        CheckConstraint(f"slug ~ '{SLUG_REGEX}'", name="slug_format"),
        CheckConstraint(in_values("status", TagStatus), name="status_valid"),
        CheckConstraint(
            f"categories <@ ARRAY[{', '.join(f"'{c.value}'" for c in VenueCategory)}]::text[]",
            name="categories_valid",
        ),
    )

    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH))
    slug: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), unique=True)
    kind: Mapped[str] = mapped_column(String(12), index=True)
    status: Mapped[str] = mapped_column(
        String(10), default=TagStatus.ACTIVE, server_default=TagStatus.ACTIVE.value
    )
    # Lower first, then A to Z.
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    emoji: Mapped[str | None] = mapped_column(String(16))
    # Other words people search with: "iced coffee", "cold brew".
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'::text[]")
    )
    # Kinds of venue the tag fits; empty = every kind. Pickers filter on it.
    categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'::text[]")
    )
    # The venue login that asked for it (status `suggested`).
    suggested_by_member_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("venue_members.id", ondelete="SET NULL")
    )


class VenueTag(Base, CreatedAtMixin):
    __tablename__ = "venue_tags"

    venue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # The venue's main tag, shown first on cards. At most one per venue.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    __table_args__ = (
        Index(
            "uq_venue_tags_primary",
            "venue_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )


class VenueItem(Base, TimestampMixin):
    __tablename__ = "venue_items"
    __table_args__ = (
        UniqueConstraint("venue_id", "position", name="uq_venue_items_venue_position"),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint("price_paise >= 0", name="price_paise_non_negative"),
    )

    venue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger)
    # As set by the venue; shown with that note.
    price_paise: Mapped[int | None] = mapped_column(BigInteger)
    # "Must try".
    is_signature: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # S3 object key, never a full URL.
    photo_s3_key: Mapped[str | None] = mapped_column(String(255))


class VisitTag(Base, CreatedAtMixin):
    __tablename__ = "visit_tags"

    visit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("visits.id", ondelete="CASCADE"), primary_key=True
    )
    # RESTRICT: a tag customers have picked cannot be deleted, so past answers stay true.
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    # Thumbs up or down on what they tried; null = not answered. Venues see counts only.
    liked: Mapped[bool | None] = mapped_column(Boolean)
