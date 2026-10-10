"""Declarative base and reusable column mixins for all ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.common.ids import CODE_LENGTH, new_code, new_uuid

# Deterministic constraint names so Alembic autogenerate produces stable migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    # Generated in Python so the id is known before flush.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Entity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Default parent for domain models: UUID primary key + timestamps."""

    __abstract__ = True


def public_code_column(prefix: str) -> Mapped[str]:
    """Unique human-readable code column, e.g. on `Venue`:

        venue_code: Mapped[str] = public_code_column("VEN")

    Name it `<entity>_code` and register the prefix in docs/WORKPLAN.md §1.1.
    """
    new_code(prefix)  # validate the prefix at import time
    return mapped_column(
        String(4 + CODE_LENGTH),
        unique=True,
        nullable=False,
        default=lambda: new_code(prefix),
    )
