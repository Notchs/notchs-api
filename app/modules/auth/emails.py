"""Email address helpers: normalisation, a light format check and the disposable-domain list."""

import re
from typing import Annotated

from pydantic import AfterValidator, Field

MAX_EMAIL_LENGTH = 120  # venue_members.email, admin_users.email, venue_applications.email

# Deliberately simple: one @, something before it, a dotted domain after it, no spaces.
# The emailed code or link is the real proof that an address works.
_EMAIL_FORMAT = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]{2,}")

# Throwaway inboxes refused on the venue application form. Extend as abuse shows up.
DISPOSABLE_DOMAINS = frozenset(
    {
        "10minutemail.com",
        "burnermail.io",
        "dispostable.com",
        "emailondeck.com",
        "fakeinbox.com",
        "getnada.com",
        "guerrillamail.com",
        "maildrop.cc",
        "mailinator.com",
        "mintemail.com",
        "mohmal.com",
        "sharklasers.com",
        "spamgourmet.com",
        "temp-mail.org",
        "tempail.com",
        "tempmail.com",
        "throwawaymail.com",
        "trashmail.com",
        "yopmail.com",
    }
)


def normalize_email(email: str) -> str:
    """Emails are stored and compared lowercase, without surrounding spaces."""
    return email.strip().lower()


def is_disposable(email: str) -> bool:
    domain = normalize_email(email).rpartition("@")[2]
    # Also catch subdomains such as `x.mailinator.com`.
    return any(domain == d or domain.endswith(f".{d}") for d in DISPOSABLE_DOMAINS)


def check_email(value: str) -> str:
    email = normalize_email(value)
    if not _EMAIL_FORMAT.fullmatch(email):
        raise ValueError("Enter a valid email address")
    return email


# Request field type: trimmed, lowercased, roughly checked.
Email = Annotated[str, Field(max_length=MAX_EMAIL_LENGTH), AfterValidator(check_email)]
