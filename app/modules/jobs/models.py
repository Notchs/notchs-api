"""One row per run of a monthly job (G5): what it did, how far it got, what failed.

A run claims its (job, period) with the unique constraint, so two API instances never run the
same month twice. Re-running a month (admin "Run again") reuses the row; every job is written
to skip work already done, so a re-run only finishes what failed.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity, in_values


class JobName(StrEnum):
    NOTCH_CARDS = "notch_cards"  # customers' cards, 1st 06:00
    VENUE_CARDS = "venue_cards"  # venues' cards + "your card is ready" emails, 1st 06:30


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # stopped, or finished with failures


class RunTrigger(StrEnum):
    SCHEDULER = "scheduler"
    ADMIN = "admin"
    CLI = "cli"


MAX_STORED_ERRORS = 50


class JobRun(Entity):
    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint("job", "period", name="uq_job_runs_job_period"),
        CheckConstraint(in_values("job", JobName), name="job_valid"),
        CheckConstraint(in_values("status", RunStatus), name="status_valid"),
        CheckConstraint(in_values("trigger", RunTrigger), name="trigger_valid"),
    )

    job: Mapped[str] = mapped_column(String(32))
    # The month the run is for: "2026-10".
    period: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(10))
    trigger: Mapped[str] = mapped_column(String(10))
    triggered_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The latest failures, newest last: [{"at": ..., "ref": "USR-…", "error": "..."}].
    errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
