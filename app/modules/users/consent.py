"""Consents and their exact wording. The bot shows these words, and the same words are stored
with each consent, so what a customer agreed to can always be shown (DPDP).

Changing any wording means a new version: add it here with the new version string.
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import (
    PRIVACY_NOTICE_VERSION,
    ConsentKind,
    SignupSource,
    User,
    UserConsent,
)

# B1, sent with an "I agree" and a "Privacy notice" button on the first ever scan.
PRIVACY_CONSENT_TEXT = (
    "Welcome to Notchs. Every real visit becomes a Notch. To start, confirm you're 18+ and "
    "agree to our privacy notice."
)
PRIVACY_CONSENT_ACCEPT = "I agree"


async def record_privacy_consent(
    session: AsyncSession, user: User, *, at: datetime, accepted_with: str
) -> UserConsent:
    """B1 accepted: 18+ confirmed and the privacy notice agreed to. Sets the customer's quick
    flags and keeps the full record."""
    user.age_confirmed_at = at
    user.consent_at = at
    user.consent_version = PRIVACY_NOTICE_VERSION
    consent = UserConsent(
        user_id=user.id,
        kind=ConsentKind.PRIVACY_NOTICE,
        wording=f"{PRIVACY_CONSENT_TEXT}\n[{PRIVACY_CONSENT_ACCEPT}] ({accepted_with})",
        version=PRIVACY_NOTICE_VERSION,
        source=SignupSource.WHATSAPP,
        created_at=at,
    )
    session.add(consent)
    await session.flush()
    return consent
