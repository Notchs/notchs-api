"""Declarative base and reusable column mixins for all ORM models."""

import uuid
from collections.abc import Iterable
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


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Entity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Default parent for domain models: UUID primary key + timestamps."""

    __abstract__ = True


class InsertOnlyEntity(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """For rows that are written once and never edited, e.g. tokens: no `updated_at`."""

    __abstract__ = True


def in_values(column: str, values: Iterable[str]) -> str:
    """SQL for a CHECK that limits a text column to a list, e.g. `status IN ('a', 'b')`.

    The sheet stores enums as VARCHAR + CHECK, so adding a value is an ordinary migration.
    """
    return f"{column} IN ({', '.join(f"'{value}'" for value in values)})"


def public_code_column(prefix: str) -> Mapped[str]:
    """Unique human-readable code column, e.g. on `Venue`:

        venue_code: Mapped[str] = public_code_column("VEN")

    Name it `<entity>_code` and add its prefix to README, "IDs and public codes".
    """
    new_code(prefix)  # validate the prefix at import time
    return mapped_column(
        String(4 + CODE_LENGTH),
        unique=True,
        nullable=False,
        default=lambda: new_code(prefix),
    )
