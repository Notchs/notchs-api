"""Recording fraud signals: patterns across scans that a person should look at (G4).

A signal never blocks a Notch by itself. The individual Notchs involved are flagged for the
proof review queue (G1) where the QR spec says so; the signal is the bigger picture.
"""

import uuid
from datetime import datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ids import new_uuid
from app.modules.notches.models import FraudSignal, SignalKind

# QR spec: "More than 6 scans a minute at one counter: flag the pattern for review."
MAX_SCANS_PER_MINUTE = 6
_MINUTE_KEY_TTL_SECONDS = 180


async def record(
    session: AsyncSession,
    kind: SignalKind,
    dedup_key: str,
    *,
    venue_id: uuid.UUID,
    counter_id: uuid.UUID | None,
    at: datetime,
    details: dict[str, Any],
) -> None:
    """Add a signal, or count one more occurrence of an existing one (same `dedup_key`).
    A signal someone already resolved stays resolved."""
    statement = insert(FraudSignal).values(
        id=new_uuid(),
        kind=kind,
        dedup_key=dedup_key[:120],
        venue_id=venue_id,
        counter_id=counter_id,
        first_seen_at=at,
        last_seen_at=at,
        occurrences=1,
        details=details,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[FraudSignal.dedup_key],
            set_={
                "occurrences": FraudSignal.occurrences + 1,
                "last_seen_at": func.greatest(
                    FraudSignal.last_seen_at, statement.excluded.last_seen_at
                ),
                "details": statement.excluded.details,
                "updated_at": func.now(),
            },
        )
    )


async def count_scan(
    session: AsyncSession,
    redis: Redis,
    *,
    venue_id: uuid.UUID,
    counter_id: uuid.UUID,
    at: datetime,
) -> int:
    """Count a scan with a valid code against its counter's minute; past the limit, record the
    burst. Returns the minute's count so far."""
    minute = int(at.timestamp() // 60)
    key = f"scans:{counter_id}:{minute}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, _MINUTE_KEY_TTL_SECONDS)
        count, _ = await pipe.execute()
    count = int(count)
    if count > MAX_SCANS_PER_MINUTE:
        await record(
            session,
            SignalKind.MANY_SCANS_MINUTE,
            f"{SignalKind.MANY_SCANS_MINUTE}:{counter_id}:{minute}",
            venue_id=venue_id,
            counter_id=counter_id,
            at=at,
            details={
                "scans": count,
                "minute_start": datetime.fromtimestamp(minute * 60, at.tzinfo).isoformat(),
                "limit": MAX_SCANS_PER_MINUTE,
            },
        )
    return count
