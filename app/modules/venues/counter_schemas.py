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
    # The last scan with a valid code: E3's live test polls this, E5 shows "last scan 17:58".
    last_scan_at: datetime | None = None
    # As the phone last reported them (E11 "On · 82%").
    battery_pct: int | None = None
    is_charging: bool | None = None


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
    area: str | None = None  # "Navrangpura", under the name on the counter screen
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
    # "14 notched here today" (E4), in the city's day.
    notched_today: int
    # Set the code clock from this: codes are made from the phone's time.
    server_time: datetime


class HeartbeatIn(BaseSchema):
    """Optional: what the phone knows about its battery (Battery Status API, where available)."""

    battery_pct: int | None = Field(default=None, ge=0, le=100)
    is_charging: bool | None = None


class HeartbeatOut(BaseSchema):
    notched_today: int
    server_time: datetime
