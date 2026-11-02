import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field

from app.common.schemas import BaseSchema, PublicCode
from app.modules.notches.models import NotchStatus, SignalKind, SignalStatus


class ReviewNotchOut(BaseSchema):
    """A Notch as the proof review queue shows it (G1). The customer by code, never number."""

    id: uuid.UUID
    notch_code: PublicCode
    status: NotchStatus
    venue_id: uuid.UUID
    venue_name: str
    area: str | None
    customer_code: str | None  # None once the customer deleted their data
    scanned_at: datetime
    local_date: date
    time: str
    code: str
    code_offset: int
    notch_no: int
    flags: list[str]
    flag_note: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None


class ReviewNotchDetailOut(ReviewNotchOut):
    # Other Notchs made with the same code at the same counter.
    same_code: list[ReviewNotchOut]


class ReviewDecisionIn(BaseSchema):
    note: str | None = Field(None, max_length=200)


class FraudSignalOut(BaseSchema):
    id: uuid.UUID
    kind: SignalKind
    status: SignalStatus
    venue_id: uuid.UUID
    venue_name: str
    counter_id: uuid.UUID | None
    first_seen_at: datetime
    last_seen_at: datetime
    occurrences: int
    details: dict[str, Any]
    resolved_by: str | None
    resolved_at: datetime | None
    resolution_note: str | None


class ResolveSignalIn(BaseSchema):
    status: Literal[SignalStatus.DISMISSED, SignalStatus.ACTIONED]
    note: str | None = Field(None, max_length=200)
