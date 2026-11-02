import uuid
from datetime import datetime
from typing import Any

from app.common.schemas import BaseSchema
from app.modules.audit.models import ActorType


class AuditEntryOut(BaseSchema):
    id: uuid.UUID
    created_at: datetime
    actor_type: ActorType
    actor_label: str
    action: str
    target_type: str
    target_id: uuid.UUID | None
    target_label: str
    details: dict[str, Any]


class OverviewOut(BaseSchema):
    """The numbers next to each admin page's name."""

    flagged_notches: int  # G1
    pending_applications: int  # G2
    deletions_in_progress: int  # G3: waiting for YES or not wiped yet
    open_fraud_signals: int  # G4
    failed_job_runs: int  # G5
    venues_by_status: dict[str, int]
    notches_today: int
