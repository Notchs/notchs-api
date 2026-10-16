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
