"""Recording consents with the exact words shown (see models.py)."""

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.consents.models import Consent, ConsentChannel, ConsentPurpose, ConsentSubject


def wording_sha256(wording: str) -> str:
    return hashlib.sha256(wording.encode("utf-8")).hexdigest()


async def record_user_consent(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    purpose: ConsentPurpose,
    version: str,
    wording: str,
    channel: ConsentChannel,
    accepted_at: datetime,
) -> None:
    session.add(
        Consent(
            subject_type=ConsentSubject.USER,
            user_id=user_id,
            purpose=purpose,
            version=version,
            wording=wording,
            wording_sha256=wording_sha256(wording),
            channel=channel,
            accepted_at=accepted_at,
        )
    )
    await session.flush()


async def delete_user_consents(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Account deletion: the account row stays as an empty shell, so its consents go here."""
    await session.execute(delete(Consent).where(Consent.user_id == user_id))
