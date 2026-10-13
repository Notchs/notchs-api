"""Cities and the areas inside them. Lookup tables: seeded, not created by users."""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity

# Lowercase words joined by single hyphens, e.g. `prahlad-nagar`. Shared by every slug column.
SLUG_REGEX = "^[a-z0-9]+(-[a-z0-9]+)*$"


class City(Entity):
    __tablename__ = "cities"
    __table_args__ = (CheckConstraint(f"slug ~ '{SLUG_REGEX}'", name="slug_format"),)

    name: Mapped[str] = mapped_column(String(80))
    # Used in public URLs: /ahmedabad/v/kahwa-house
    slug: Mapped[str] = mapped_column(String(80), unique=True)


class Area(Entity):
    __tablename__ = "areas"
    __table_args__ = (
        UniqueConstraint("city_id", "slug", name="uq_areas_city_id_slug"),
        CheckConstraint(f"slug ~ '{SLUG_REGEX}'", name="slug_format"),
    )

    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cities.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    slug: Mapped[str] = mapped_column(String(80))
