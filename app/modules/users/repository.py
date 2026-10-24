"""Data access for customers."""

from sqlalchemy import exists, select

from app.common.repository import BaseRepository
from app.modules.users.models import User


class UserRepository(BaseRepository[User]):
    model = User

    async def handle_exists(self, handle: str) -> bool:
        return bool(await self.session.scalar(select(exists().where(User.handle == handle))))
