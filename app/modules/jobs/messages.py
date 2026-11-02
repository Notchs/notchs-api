"""Emails the jobs send to venue owners (Page Inventory §7). Plain text, like auth's."""

from datetime import datetime

from app.clients.email import EmailMessage
from app.core.config import settings


def venue_card_ready_email(
    *, to: str, name: str, venue_name: str, month: str, visitors: int, new_percent: int
) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject=f"{venue_name}'s {month} Notch Card is ready",
        text=(
            f"Hi {name},\n\n"
            f"{venue_name}'s {month}: {visitors} visitors, {new_percent}% new. Every one a "
            "verified visit at your counter.\n\n"
            f"Share it on Instagram from your venue app: {settings.VENUE_APP_URL}/card\n\n"
            "— Team Notchs"
        ),
    )


def counter_off_email(
    *, to: str, name: str, venue_name: str, last_seen: datetime | None, last_seen_label: str
) -> EmailMessage:
    seen = f" Last seen {last_seen_label}." if last_seen else ""
    return EmailMessage(
        to=to,
        subject=f"Counter phone is off at {venue_name}",
        text=(
            f"Hi {name},\n\n"
            f"The counter phone at {venue_name} has stopped checking in.{seen} Customers can't "
            "notch until it's back on. Check it's plugged in and showing the QR.\n\n"
            f"Counter status: {settings.VENUE_APP_URL}/home\n\n"
            "— Team Notchs"
        ),
    )
