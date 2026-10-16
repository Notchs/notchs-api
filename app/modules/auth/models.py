"""Stored login tokens. Only SHA-256 hashes are kept, never the raw token."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, SmallInteger, String
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import InsertOnlyEntity, in_values
from app.modules.auth.enums import SubjectType, TokenType


def _subject_matches(subject: SubjectType, column: str) -> str:
    return f"((subject_type = '{subject.value}') = ({column} IS NOT NULL))"


class AuthToken(InsertOnlyEntity):
    """From the sheet, plus `admin_user_id` and `family_id`, and the token types auth needs.

    Exactly one of `user_id`, `venue_member_id`, `admin_user_id` is set, the one that matches
    `subject_type`.
    """

    __tablename__ = "auth_tokens"
    __table_args__ = (
        CheckConstraint(in_values("subject_type", SubjectType), name="subject_type_valid"),
        CheckConstraint(in_values("token_type", TokenType), name="token_type_valid"),
        CheckConstraint(
            " AND ".join(
                (
                    _subject_matches(SubjectType.USER, "user_id"),
                    _subject_matches(SubjectType.VENUE_MEMBER, "venue_member_id"),
                    _subject_matches(SubjectType.ADMIN, "admin_user_id"),
                )
            ),
            name="subject_matches_type",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )

    subject_type: Mapped[str] = mapped_column(String(16), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    venue_member_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("venue_members.id", ondelete="CASCADE"), index=True
    )
    # Not in the sheet: admins log in with the same session tokens.
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), index=True
    )
    token_type: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    # Not in the sheet. Refresh tokens only: every rotation keeps the family, and reuse of an
    # already-rotated token revokes the whole family (a stolen-cookie signal).
    family_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
