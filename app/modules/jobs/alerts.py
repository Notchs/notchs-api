"""'Counter phone is off' emails (QR spec: "Counter silent for 10+ minutes in opening hours:
alert the owner"). One email per outage: the phone's next heartbeat clears the flag."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.clients.email import EmailClient, EmailMessage
from app.core.logging import get_logger
from app.modules.jobs.messages import counter_off_email
from app.modules.jobs.runner import SessionFactory
from app.modules.locations.models import City
from app.modules.locations.time import local
from app.modules.venues.hours import status_at
from app.modules.venues.models import (
    CounterDevice,
    Venue,
    VenueMember,
    VenueMemberRole,
    VenueStatus,
)

logger = get_logger(__name__)

SILENT_FOR = timedelta(minutes=10)


async def _claim(
    session_factory: SessionFactory,
    counter: CounterDevice,
    venue: Venue,
    tz: str | None,
    now: datetime,
) -> list[EmailMessage] | None:
    """Mark the outage as alerted and commit it before any email goes out, so two API
    instances or a retry never email twice. None if someone else claimed it first."""
    async with session_factory() as session:
        claimed = await session.scalar(
            update(CounterDevice)
            .where(CounterDevice.id == counter.id, CounterDevice.offline_alerted_at.is_(None))
            .values(offline_alerted_at=now)
            .returning(CounterDevice.id)
        )
        if claimed is None:
            return None
        owners = await session.scalars(
            select(VenueMember).where(
                VenueMember.venue_id == venue.id,
                VenueMember.role == VenueMemberRole.OWNER,
                VenueMember.is_active.is_(True),
            )
        )
        seen = local(counter.last_seen_at, tz).strftime("%H:%M") if counter.last_seen_at else ""
        messages = [
            counter_off_email(
                to=owner.email,
                name=owner.name,
                venue_name=venue.name,
                last_seen=counter.last_seen_at,
                last_seen_label=seen,
            )
            for owner in owners
        ]
        await session.commit()
    return messages


async def alert_offline_counters(
    session_factory: SessionFactory, email: EmailClient | None = None
) -> int:
    """Email owners whose counter phone went quiet while the venue is open. Returns how many
    counters were alerted. Venues without opening hours are never alerted (we can't know)."""
    email = email or EmailClient()
    now = datetime.now(UTC)
    alerted = 0
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(CounterDevice, Venue, City.timezone)
                .join(Venue, Venue.id == CounterDevice.venue_id)
                .join(City, City.id == Venue.city_id)
                .where(
                    CounterDevice.revoked_at.is_(None),
                    CounterDevice.is_active.is_(True),
                    CounterDevice.token_hash.is_not(None),
                    CounterDevice.offline_alerted_at.is_(None),
                    CounterDevice.last_seen_at < now - SILENT_FOR,
                    Venue.status == VenueStatus.COUNTER_LIVE,
                    Venue.deleted_at.is_(None),
                )
            )
        ).all()
    for counter, venue, tz in rows:
        if status_at(venue.opening_hours, local(now, tz)).is_open is not True:
            continue
        try:
            messages = await _claim(session_factory, counter, venue, tz, now)
        except Exception as exc:
            logger.error("Counter-off alert for venue %s failed: %s", venue.id, type(exc).__name__)
            continue
        if messages is None:
            continue  # another API instance claimed it
        try:
            for message in messages:
                await email.send(message)
            alerted += 1
        except Exception as exc:
            # Not sent: release the claim so the next round tries again.
            async with session_factory() as session:
                await session.execute(
                    update(CounterDevice)
                    .where(CounterDevice.id == counter.id)
                    .values(offline_alerted_at=None)
                )
                await session.commit()
            # The error's type only: provider errors can quote the address they failed on.
            logger.error("Counter-off alert for venue %s failed: %s", venue.id, type(exc).__name__)
    return alerted
