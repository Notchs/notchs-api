"""Data access for customers."""

import uuid
from datetime import datetime

from sqlalchemy import exists, select, update

from app.common.repository import BaseRepository
from app.modules.users.models import User


class UserRepository(BaseRepository[User]):
    model = User

    async def handle_exists(self, handle: str) -> bool:
        return bool(await self.session.scalar(select(exists().where(User.handle == handle))))

    async def record_consent(self, user_id: uuid.UUID, version: str, now: datetime) -> bool:
        """True if this call recorded it. A double tap keeps the first time and returns False."""
        found = await self.session.scalar(
            update(User)
            .where(User.id == user_id, User.consent_at.is_(None))
            .values(consent_at=now, consent_version=version, age_confirmed_at=now)
            .returning(User.id)
            .execution_options(synchronize_session=False)
        )
        return found is not None
