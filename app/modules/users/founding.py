"""Founding Member numbers: 1 to 500, given in join order.

A customer gets the next number the first time they either join on the website (A1 stop 5,
A14) or get their first Notch, whichever comes first. The website's "358 left" counts down
from the same numbers, so it is always real. (If the holder of the highest number deletes
their data, that one number is given again; any other freed number is not.)
"""

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.modules.users.models import MAX_FOUNDING_MEMBER_NO, User

_ATTEMPTS = 5


async def seats_left(session: AsyncSession) -> int:
    """Numbers not given yet."""
    highest = await session.scalar(select(func.max(User.founding_member_no))) or 0
    return max(0, MAX_FOUNDING_MEMBER_NO - highest)


async def assign(session: AsyncSession, user: User) -> int | None:
    """Give `user` the next number if they have none and seats are left. Returns their number
    (new or existing), or None. Two customers joining at once never get the same number: the
    unique constraint refuses the second, which then takes the next one."""
    if user.founding_member_no is not None:
        return user.founding_member_no
    for _ in range(_ATTEMPTS):
        highest = await session.scalar(select(func.max(User.founding_member_no))) or 0
        number = highest + 1
        if number > MAX_FOUNDING_MEMBER_NO:
            return None
        try:
            async with session.begin_nested():
                result = await session.execute(
                    update(User)
                    .where(User.id == user.id, User.founding_member_no.is_(None))
                    .values(founding_member_no=number)
                    .execution_options(synchronize_session=False)
                )
        except IntegrityError:
            continue  # someone else took `number` a moment ago
        if result.rowcount:
            # Already in the database: record it on the object without another UPDATE.
            set_committed_value(user, "founding_member_no", number)
            return number
        # Another message from the same customer assigned one first.
        await session.refresh(user, ["founding_member_no"])
        return user.founding_member_no
    return None
