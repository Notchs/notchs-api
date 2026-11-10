"""tags, venue tags, venue items and visit tags

Scans module step 6:
- tags: the shared list, kept by admins (spec sheet, plus the kind `item` and updated_at).
- venue_tags: what a place is (spec sheet). At most 5, enforced in the service.
- venue_items: what you can try there, in menu order. At most 10, kind `item` only, enforced in the
  service. Not in the sheet.
- visit_tags: what a customer said they tried. Not in the sheet. RESTRICT on the tag: one customers
  have picked cannot be deleted.

No starter list: admins add every tag. Autogenerate does not detect CHECK constraints, so those are
written out here.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.modules.locations.models import SLUG_REGEX

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('cuisine', 'vibe', 'activity', 'item')", name=op.f("ck_tags_kind_valid")
        ),
        sa.CheckConstraint(f"slug ~ '{SLUG_REGEX}'", name=op.f("ck_tags_slug_format")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("slug", name=op.f("uq_tags_slug")),
    )
    op.create_index(op.f("ix_tags_kind"), "tags", ["kind"], unique=False)
    op.create_table(
        "venue_tags",
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("fk_venue_tags_tag_id_tags"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name=op.f("fk_venue_tags_venue_id_venues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("venue_id", "tag_id", name=op.f("pk_venue_tags")),
    )
    op.create_index(op.f("ix_venue_tags_tag_id"), "venue_tags", ["tag_id"], unique=False)
    op.create_table(
        "venue_items",
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_venue_items_position_non_negative")),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("fk_venue_items_tag_id_tags"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name=op.f("fk_venue_items_venue_id_venues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("venue_id", "tag_id", name=op.f("pk_venue_items")),
        sa.UniqueConstraint("venue_id", "position", name="uq_venue_items_venue_position"),
    )
    op.create_index(op.f("ix_venue_items_tag_id"), "venue_items", ["tag_id"], unique=False)
    op.create_table(
        "visit_tags",
        sa.Column("visit_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("fk_visit_tags_tag_id_tags"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["visit_id"],
            ["visits.id"],
            name=op.f("fk_visit_tags_visit_id_visits"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("visit_id", "tag_id", name=op.f("pk_visit_tags")),
    )
    op.create_index(op.f("ix_visit_tags_tag_id"), "visit_tags", ["tag_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_visit_tags_tag_id"), table_name="visit_tags")
    op.drop_table("visit_tags")
    op.drop_index(op.f("ix_venue_items_tag_id"), table_name="venue_items")
    op.drop_table("venue_items")
    op.drop_index(op.f("ix_venue_tags_tag_id"), table_name="venue_tags")
    op.drop_table("venue_tags")
    op.drop_index(op.f("ix_tags_kind"), table_name="tags")
    op.drop_table("tags")
