"""The privacy notice and 18+ confirmation customers agree to on WhatsApp (DPDP).

DRAFT: the team has not approved this wording or the notice page yet. Change the version whenever
the wording changes. `users.consent_version` says which one a customer agreed to, and the exact text
they were shown is stored with the outbound message in `whatsapp_messages`.
"""

from app.core.config import settings

CONSENT_VERSION = "draft-2026-11"


def consent_text() -> str:
    return (
        "We use your WhatsApp number and name to keep your stamps, points and rank. Your profile "
        f"is private unless you make it public. Privacy notice: {settings.CUSTOMER_APP_URL}/privacy"
        "\n\nTap below to confirm you agree and are 18 or over."
    )
