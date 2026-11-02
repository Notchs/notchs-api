"""The monthly jobs, run on the 1st for the month that just ended (`period` = "2026-10").

    notch_cards           a card for every customer with a Notch that month (C9)
    venue_cards           a card for every venue with a Notch that month, and the owner's
                          "Your Notch Card is ready" email (E7, §7)

Each item gets its own transaction, so one failure never undoes the rest, and each job skips
what is already done, so a re-run only finishes what failed.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from app.clients.email import EmailClient, EmailMessage
from app.modules.cards.builder import build_card
from app.modules.cards.models import NotchCard, VenueCard
from app.modules.jobs.messages import venue_card_ready_email
from app.modules.jobs.models import JobName
from app.modules.jobs.runner import JobFunction, Progress, SessionFactory
from app.modules.locations.models import City
from app.modules.locations.time import Period, month_from_string, month_label
from app.modules.notches.models import COUNTED_STATUSES, Notch
from app.modules.users.models import User, UserStatus
from app.modules.venues.models import Venue, VenueMember, VenueMemberRole


async def notch_cards(session_factory: SessionFactory, period: str, progress: Progress) -> None:
    month = month_from_string(period)
    async with session_factory() as session:
        rows = await session.execute(
            select(Notch.user_id, Notch.city_id, func.count())
            .where(
                Notch.status.in_(COUNTED_STATUSES),
                Notch.user_id.is_not(None),
                Notch.local_date.between(month.start, month.end),
            )
            .group_by(Notch.user_id, Notch.city_id)
        )
        # A customer who notched in two cities gets the card of the one they notched most in.
        best: dict[uuid.UUID, tuple[uuid.UUID, int]] = {}
        for user_id, city_id, count in rows:
            if user_id not in best or count > best[user_id][1]:
                best[user_id] = (city_id, count)
        done = set(
            await session.scalars(select(NotchCard.user_id).where(NotchCard.period == month.start))
        )
        next_no = (
            await session.scalar(
                select(func.max(NotchCard.card_no)).where(NotchCard.period == month.start)
            )
            or 0
        ) + 1
    progress.total = len(best)
    for user_id in sorted(best, key=str):
        if user_id in done:
            await progress.ok()
            continue
        try:
            async with session_factory() as session:
                user = await session.get(User, user_id)
                city = await session.get(City, best[user_id][0])
                if user is None or city is None or user.status != UserStatus.ACTIVE:
                    await progress.ok()  # deleted or blocked since: no card
                    continue
                card = await build_card(session, user, city, month, next_no)
                if card is not None:
                    session.add(card)
                    await session.commit()
                    next_no += 1
            await progress.ok()
        except Exception as exc:
            await progress.fail(str(user_id), exc)


async def venue_cards(session_factory: SessionFactory, period: str, progress: Progress) -> None:
    month = month_from_string(period)
    label = month_label(month.start)
    email = EmailClient()
    async with session_factory() as session:
        venue_ids = list(
            await session.scalars(
                select(Notch.venue_id)
                .where(
                    Notch.status.in_(COUNTED_STATUSES),
                    Notch.local_date.between(month.start, month.end),
                )
                .group_by(Notch.venue_id)
            )
        )
    progress.total = len(venue_ids)
    for venue_id in venue_ids:
        try:
            emails = await _venue_card(session_factory, venue_id, month, label)
        except Exception as exc:
            await progress.fail(str(venue_id), exc)
            continue
        try:
            for message in emails:
                await email.send(message)
        except Exception as exc:
            # Not sent: release the claim so a re-run tries this venue's email again.
            async with session_factory() as session:
                await session.execute(
                    update(VenueCard)
                    .where(VenueCard.venue_id == venue_id, VenueCard.period == month.start)
                    .values(emailed_at=None)
                )
                await session.commit()
            await progress.fail(str(venue_id), exc)
            continue
        await progress.ok()


async def _venue_card(
    session_factory: SessionFactory, venue_id: uuid.UUID, month: Period, label: str
) -> list[EmailMessage]:
    """Make the venue's card if it is missing, and claim its email: `emailed_at` is committed
    before anything is sent, so a retry or a second run never emails the owner twice. Returns
    the emails to send (none if they were sent already)."""
    async with session_factory() as session:
        venue = await session.get(Venue, venue_id)
        card = await session.scalar(
            select(VenueCard)
            .where(VenueCard.venue_id == venue_id, VenueCard.period == month.start)
            .with_for_update()
        )
        if card is None:
            visitors, anonymous, new, notches = (
                await session.execute(
                    select(
                        func.count(func.distinct(Notch.user_id)),
                        func.count().filter(Notch.user_id.is_(None)),
                        func.count().filter(Notch.user_visit_no == 1),
                        func.count(),
                    ).where(
                        Notch.venue_id == venue_id,
                        Notch.status.in_(COUNTED_STATUSES),
                        Notch.local_date.between(month.start, month.end),
                    )
                )
            ).one()
            card = VenueCard(
                venue_id=venue_id,
                period=month.start,
                visitors=visitors + anonymous,
                new_visitors=new,
                notches=notches,
            )
            session.add(card)
            await session.flush()
        emails: list[EmailMessage] = []
        if card.emailed_at is None and venue is not None and venue.deleted_at is None:
            owners = await session.scalars(
                select(VenueMember).where(
                    VenueMember.venue_id == venue_id,
                    VenueMember.role == VenueMemberRole.OWNER,
                    VenueMember.is_active.is_(True),
                )
            )
            percent = round(card.new_visitors / card.visitors * 100) if card.visitors else 0
            emails = [
                venue_card_ready_email(
                    to=owner.email,
                    name=owner.name,
                    venue_name=venue.name,
                    month=label,
                    visitors=card.visitors,
                    new_percent=percent,
                )
                for owner in owners
            ]
            card.emailed_at = datetime.now(UTC)
        await session.commit()
    return emails


JOBS: dict[JobName, JobFunction] = {
    JobName.NOTCH_CARDS: notch_cards,
    JobName.VENUE_CARDS: venue_cards,
}
