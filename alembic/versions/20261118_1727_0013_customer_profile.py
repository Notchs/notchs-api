"""customer profile

The customer's profile (passport) gets a picture and an optional date of birth (team decisions,
18 Nov 2026):
- users.photo_s3_key: the picture's key in the private S3 bucket (never a URL).
- users.birth_date: optional, added by the customer for birthday treats and their age range
  (worked out, not stored). 18+ is checked by the API; the database refuses dates before 1900.
- consents.purpose gets `birthday`: saving a date of birth records the exact consent wording.
No rows are changed.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-18 17:27:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_PURPOSES = "purpose IN ('privacy_notice', 'venue_application_privacy')"
NEW_PURPOSES = "purpose IN ('privacy_notice', 'venue_application_privacy', 'birthday')"


def upgrade() -> None:
    op.add_column('users', sa.Column('photo_s3_key', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('birth_date', sa.Date(), nullable=True))
    op.create_check_constraint(
        op.f('ck_users_birth_date_range'), 'users', "birth_date >= DATE '1900-01-01'"
    )
    op.drop_constraint(op.f('ck_consents_purpose_valid'), 'consents', type_='check')
    op.create_check_constraint(op.f('ck_consents_purpose_valid'), 'consents', NEW_PURPOSES)


def downgrade() -> None:
    # Birthday consents only exist with the birthday column: they go with it.
    op.execute("DELETE FROM consents WHERE purpose = 'birthday'")
    op.drop_constraint(op.f('ck_consents_purpose_valid'), 'consents', type_='check')
    op.create_check_constraint(op.f('ck_consents_purpose_valid'), 'consents', OLD_PURPOSES)
    op.drop_constraint(op.f('ck_users_birth_date_range'), 'users', type_='check')
    op.drop_column('users', 'birth_date')
    op.drop_column('users', 'photo_s3_key')
