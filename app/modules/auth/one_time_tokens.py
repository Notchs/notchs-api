"""Single-use link tokens stored in `auth_tokens`: set password, password reset, and later the
customer magic link and device pairing. Only the SHA-256 hash is stored.
"""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.enums import RevokeReason, SubjectType, TokenType
from app.modules.auth.exceptions import TokenInvalidException
from app.modules.auth.models import AuthToken
from app.modules.auth.sessions import SUBJECT_COLUMN, Subject
from app.modules.auth.tokens import hash_token, lifetime, new_token


class OneTimeTokenService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(self, subject: Subject, token_type: TokenType, *, ip: str | None = None) -> str:
        """Create a token and return the raw value for the link. Older unused tokens of the same
        type for this subject stop working, so only the newest email's link is valid."""
        now = datetime.now(UTC)
        await self.session.execute(
            update(AuthToken)
            .where(
                SUBJECT_COLUMN[subject.type] == subject.id,
                AuthToken.token_type == token_type,
                AuthToken.used_at.is_(None),
                AuthToken.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoke_reason=RevokeReason.SUPERSEDED)
        )
        raw = new_token()
        self.session.add(
            AuthToken(
                subject_type=subject.type,
                **{SUBJECT_COLUMN[subject.type].key: subject.id},
                token_type=token_type,
                token_hash=hash_token(raw),
                expires_at=now + lifetime(token_type),
                ip_address=ip,
            )
        )
        await self.session.flush()
        return raw

    async def consume(
        self, raw: str, token_types: tuple[TokenType, ...], subject_type: SubjectType
    ) -> AuthToken:
        """Use a token once. Raises TokenInvalid if it is unknown, used, revoked or expired."""
        token = await self.session.scalar(
            select(AuthToken)
            .where(
                AuthToken.token_hash == hash_token(raw),
                AuthToken.token_type.in_(token_types),
                AuthToken.subject_type == subject_type,
            )
            .with_for_update()
        )
        now = datetime.now(UTC)
        if (
            token is None
            or token.used_at is not None
            or token.revoked_at is not None
            or token.expires_at <= now
        ):
            raise TokenInvalidException()
        token.used_at = now
        await self.session.flush()
        return token
