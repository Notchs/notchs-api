"""Writing to the audit log. Call it inside the same transaction as the change it records, so
a change and its audit row are kept or lost together."""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dependencies import PaginationParams
from app.modules.admin_users.models import AdminUser
from app.modules.audit.models import ActorType, AuditLog

SYSTEM_LABEL = "System"


async def record(
    session: AsyncSession,
    *,
    actor: AdminUser | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    target_label: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Record an admin's action, or the system's when `actor` is None."""
    session.add(
        AuditLog(
            actor_type=ActorType.ADMIN if actor else ActorType.SYSTEM,
            actor_id=actor.id if actor else None,
            actor_label=actor.name if actor else SYSTEM_LABEL,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_label=target_label[:160],
            details=details or {},
        )
    )
    await session.flush()


async def entries(
    session: AsyncSession,
    params: PaginationParams,
    *,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    actor_type: ActorType | None = None,
) -> tuple[Sequence[AuditLog], int]:
    """Newest first (G6)."""
    conditions = []
    if target_type:
        conditions.append(AuditLog.target_type == target_type)
    if target_id:
        conditions.append(AuditLog.target_id == target_id)
    if actor_type:
        conditions.append(AuditLog.actor_type == actor_type)
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(*conditions)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(params.offset)
            .limit(params.size)
        )
    ).all()
    total = await session.scalar(select(func.count()).select_from(AuditLog).where(*conditions))
    return rows, total or 0
