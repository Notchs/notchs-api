"""cities areas admin_users venues

First tables: the venues table from the spec, plus the three tables it points at.
Written by hand. `notchs_app` gets its DML rights from the migrator's default privileges
(set up once on the database), so no GRANT is needed here.

Revision ID: 0001
Revises:
Create Date: 2026-10-14 02:41:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SLUG_FORMAT = "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "cities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cities")),
        sa.UniqueConstraint("slug", name=op.f("uq_cities_slug")),
        sa.CheckConstraint(SLUG_FORMAT, name=op.f("ck_cities_slug_format")),
    )

    op.create_table(
        "areas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("city_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_areas")),
        sa.ForeignKeyConstraint(
            ["city_id"], ["cities.id"], name=op.f("fk_areas_city_id_cities")
        ),
        sa.UniqueConstraint("city_id", "slug", name="uq_areas_city_id_slug"),
        sa.CheckConstraint(SLUG_FORMAT, name=op.f("ck_areas_slug_format")),
    )
    op.create_index(op.f("ix_areas_city_id"), "areas", ["city_id"])

    op.create_table(
        "admin_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("admin_user_code", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_users")),
        sa.UniqueConstraint("admin_user_code", name=op.f("uq_admin_users_admin_user_code")),
        sa.UniqueConstraint("email", name=op.f("uq_admin_users_email")),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_admin_users_email_lowercase")),
    )

    op.create_table(
        "venues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("venue_code", sa.String(length=12), nullable=False),
        sa.Column("city_id", sa.Uuid(), nullable=False),
        sa.Column("area_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("geofence_radius_m", sa.Integer(), server_default="150", nullable=False),
        sa.Column("description", sa.String(length=160), nullable=True),
        sa.Column("photo_s3_key", sa.String(length=255), nullable=True),
        sa.Column("opening_hours", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=14), server_default="pending", nullable=False),
        sa.Column("founding_venue_no", sa.Integer(), nullable=True),
        sa.Column("first_notch_spots", sa.Integer(), server_default="50", nullable=False),
        sa.Column("free_until", sa.Date(), nullable=True),
        sa.Column("monthly_fee_paise", sa.BigInteger(), nullable=True),
        sa.Column("signed_by_admin_id", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_venues")),
        sa.ForeignKeyConstraint(
            ["city_id"], ["cities.id"], name=op.f("fk_venues_city_id_cities")
        ),
        sa.ForeignKeyConstraint(
            ["area_id"], ["areas.id"], name=op.f("fk_venues_area_id_areas")
        ),
        sa.ForeignKeyConstraint(
            ["signed_by_admin_id"],
            ["admin_users.id"],
            name=op.f("fk_venues_signed_by_admin_id_admin_users"),
        ),
        sa.UniqueConstraint("venue_code", name=op.f("uq_venues_venue_code")),
        sa.UniqueConstraint("city_id", "slug", name="uq_venues_city_id_slug"),
        sa.UniqueConstraint("founding_venue_no", name=op.f("uq_venues_founding_venue_no")),
        sa.CheckConstraint(SLUG_FORMAT, name=op.f("ck_venues_slug_format")),
        sa.CheckConstraint(
            "category IN ('cafe', 'restaurant', 'bar', 'hotel', 'turf_gym', 'other')",
            name=op.f("ck_venues_category_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'signed', 'counter_live', 'paused', 'closed')",
            name=op.f("ck_venues_status_valid"),
        ),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name=op.f("ck_venues_latitude_range")),
        sa.CheckConstraint(
            "longitude BETWEEN -180 AND 180", name=op.f("ck_venues_longitude_range")
        ),
        sa.CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)",
            name=op.f("ck_venues_coordinates_paired"),
        ),
        sa.CheckConstraint(
            "geofence_radius_m > 0", name=op.f("ck_venues_geofence_radius_positive")
        ),
        sa.CheckConstraint(
            "founding_venue_no BETWEEN 1 AND 30", name=op.f("ck_venues_founding_venue_no_range")
        ),
        sa.CheckConstraint(
            "first_notch_spots >= 0", name=op.f("ck_venues_first_notch_spots_non_negative")
        ),
        sa.CheckConstraint(
            "monthly_fee_paise >= 0", name=op.f("ck_venues_monthly_fee_paise_non_negative")
        ),
    )
    op.create_index(op.f("ix_venues_city_id"), "venues", ["city_id"])
    op.create_index(op.f("ix_venues_area_id"), "venues", ["area_id"])
    op.create_index(op.f("ix_venues_category"), "venues", ["category"])
    op.create_index(op.f("ix_venues_status"), "venues", ["status"])


def downgrade() -> None:
    op.drop_table("venues")
    op.drop_table("admin_users")
    op.drop_table("areas")
    op.drop_table("cities")
