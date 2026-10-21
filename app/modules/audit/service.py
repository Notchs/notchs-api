"""Recording activity in the audit log, and reading it back for the admin panel.

    await record(session, Actor.admin(admin), "application.approve", application,
                 after={"status": "approved"})

The entry is added to the caller's session, so it is saved in the same transaction as the change
it describes: if the change is rolled back, so is the entry. When a failure must still be recorded
(a failed login), the caller commits before raising.

The request's IP and id are filled in from the request context. Values are cleaned before saving:
keys that look like phone numbers, passwords, tokens or secrets are dropped, and ids, dates and
decimals become strings.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.dependencies import PaginationParams
from app.middleware.request_context import current_request
from app.modules.audit.models import ActorType, AuditLog

# Never stored, at any depth. Matched on the key, case-insensitively, as a substring.
SENSITIVE_KEY_PARTS = ("phone", "password", "token", "secret", "otp", "code_hash", "totp")
MAX_ENTITY_ID_LENGTH = 40


@dataclass(frozen=True, slots=True)
class Actor:
    type: ActorType
    id: uuid.UUID | None = None

    @classmethod
    def admin(cls, admin: Any) -> Actor:
        return cls(ActorType.ADMIN, admin.id)

    @classmethod
    def venue_member(cls, member: Any) -> Actor:
        return cls(ActorType.VENUE_MEMBER, member.id)

    @classmethod
    def user(cls, user: Any) -> Actor:
        return cls(ActorType.USER, user.id)

    @classmethod
    def device(cls, counter: Any) -> Actor:
        return cls(ActorType.DEVICE, counter.id)

    @classmethod
    def of(cls, kind: str, id_: uuid.UUID | None) -> Actor:
        """From an auth subject type (`user`, `venue_member`, `admin`) and id."""
        return cls(ActorType(kind), id_)


SYSTEM = Actor(ActorType.SYSTEM)
PUBLIC = Actor(ActorType.PUBLIC)


@dataclass(frozen=True, slots=True)
class Entity:
    """The record acted on, when there is no ORM object at hand."""

    type: str  # table name
    id: Any = None


def _entity(target: Any) -> tuple[str, str | None]:
    if isinstance(target, Entity):
        return target.type, None if target.id is None else str(target.id)[:MAX_ENTITY_ID_LENGTH]
    mapper = inspect(target).mapper  # an ORM object
    return mapper.local_table.name, str(target.id)[:MAX_ENTITY_ID_LENGTH]


def clean(value: Any) -> Any:
    """JSON-safe, with sensitive keys removed."""
    if isinstance(value, Mapping):
        return {
            str(key): clean(item)
            for key, item in value.items()
            if not any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS)
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [clean(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


async def record(
    session: AsyncSession,
    actor: Actor,
    action: str,
    target: Any,
    *,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
) -> AuditLog:
    """Add an entry to the session. `target` is an ORM object or an `Entity`."""
    entity_type, entity_id = _entity(target)
    request = current_request()
    entry = AuditLog(
        actor_type=actor.type,
        actor_id=actor.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=clean(before) if before else None,
        after=clean(after) if after else None,
        ip_address=request.ip if request else None,
        request_id=request.request_id if request else None,
    )
    session.add(entry)
    return entry


@dataclass(frozen=True, slots=True)
class AuditFilters:
    actor_type: ActorType | None = None
    actor_id: uuid.UUID | None = None
    action: str | None = None  # exact, or a prefix ending in "." such as "auth."
    entity_type: str | None = None
    entity_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class AuditLogService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(
        self, params: PaginationParams, filters: AuditFilters
    ) -> tuple[Sequence[AuditLog], int]:
        """Newest first."""
        where = []
        if filters.actor_type is not None:
            where.append(AuditLog.actor_type == filters.actor_type)
        if filters.actor_id is not None:
            where.append(AuditLog.actor_id == filters.actor_id)
        if filters.action:
            where.append(
                AuditLog.action.startswith(filters.action, autoescape=True)
                if filters.action.endswith(".")
                else AuditLog.action == filters.action
            )
        if filters.entity_type:
            where.append(AuditLog.entity_type == filters.entity_type)
        if filters.entity_id:
            where.append(AuditLog.entity_id == filters.entity_id)
        if filters.since is not None:
            where.append(AuditLog.created_at >= filters.since)
        if filters.until is not None:
            where.append(AuditLog.created_at < filters.until)

        items = await self.session.scalars(
            select(AuditLog)
            .where(*where)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(params.offset)
            .limit(params.size)
        )
        total = await self.session.scalar(select(func.count()).select_from(AuditLog).where(*where))
        return items.all(), total or 0
