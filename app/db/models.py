"""Model registry for Alembic autogenerate.

Import every module's models here so they are attached to `Base.metadata`, e.g.:

    from app.modules.users.models import User  # noqa: F401
"""

from app.db.base import Base
from app.modules.admin_users.models import AdminUser
from app.modules.locations.models import Area, City
from app.modules.venues.models import Venue

__all__ = ["AdminUser", "Area", "Base", "City", "Venue"]
