import uuid
from datetime import datetime

from app.common.schemas import BaseSchema, PublicCode
from app.modules.notches.models import NotchStatus
from app.modules.users.models import DeletionSource, DeletionStatus, UserStatus


class AdminUserRowOut(BaseSchema):
    id: uuid.UUID
    user_code: PublicCode
    phone: str  # masked: "•••• 3210"
    name: str | None
    status: UserStatus
    founding_member_no: int | None
    created_at: datetime
    last_seen_at: datetime | None


class AdminVisitOut(BaseSchema):
    notch_code: PublicCode
    venue_name: str
    scanned_at: datetime
    status: NotchStatus


class AdminUserOut(AdminUserRowOut):
    area: str | None
    is_public: bool
    signup_source: str
    consent_at: datetime | None
    notches_total: int
    badges: list[str]
    visits: list[AdminVisitOut]  # the latest 50


class DeletionOut(BaseSchema):
    id: uuid.UUID
    user_code: str
    phone: str  # "•••• 7781"
    source: DeletionSource
    status: DeletionStatus
    requested_at: datetime
    confirmed_at: datetime | None
    completed_at: datetime | None
