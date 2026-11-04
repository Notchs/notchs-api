"""customer Instagram username and whether venues may see it

Team decision (Q35): a customer may give their Instagram username and choose to show it to the
venues they visit. Off by default. Autogenerate does not detect CHECK constraints, so the format
check is written out here.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-04 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.modules.users.models import INSTAGRAM_REGEX

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("instagram_handle", sa.String(length=30), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "show_instagram_to_venues",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_users_instagram_handle_format"),
        "users",
        f"instagram_handle ~ '{INSTAGRAM_REGEX}'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_instagram_handle_format"), "users", type_="check")
    op.drop_column("users", "show_instagram_to_venues")
    op.drop_column("users", "instagram_handle")
