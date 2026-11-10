import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.common.schemas import BaseSchema, PublicCode


class CounterOut(BaseSchema):
    """The venue's counter phone, for the owner's dashboard."""

    id: uuid.UUID
    label: str
    paired: bool
    paired_at: datetime | None
    last_seen_at: datetime | None
    # Checked in within the last 2 minutes.
    online: bool
    code_interval_s: int


class PairingLinkOut(BaseSchema):
    """Open on the counter phone (or show as a QR the phone scans). Works once."""

    url: str
    expires_at: datetime


class CounterRenameIn(BaseSchema):
    label: str = Field(min_length=1, max_length=40)


class DisplayVenueOut(BaseSchema):
    name: str
    venue_code: PublicCode
    slug: str
    scan_code: str


class DisplayScanOut(BaseSchema):
    """What the QR on the counter phone opens: WhatsApp with the message pre-typed.

    Replace `{code}` in `link_template` with the live 6-digit code (same as `totp`) and draw the
    result as a QR; redraw it whenever the code changes. `text_example` shows the message a
    customer ends up sending.
    """

    whatsapp_number: str
    message: str
    link_template: str
    text_example: str


class TotpSettingsOut(BaseSchema):
    """What the phone needs to make the codes itself, offline (RFC 6238 TOTP)."""

    secret: str  # base32
    digits: int = 6
    interval_s: int
    algorithm: Literal["SHA1"] = "SHA1"


class DisplayConfigOut(BaseSchema):
    counter_id: uuid.UUID
    label: str
    venue: DisplayVenueOut
    totp: TotpSettingsOut
    # None until WHATSAPP_PUBLIC_NUMBER is set (the display then can't show a scan QR).
    scan: DisplayScanOut | None
