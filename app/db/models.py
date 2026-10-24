"""Model registry for Alembic autogenerate.

Import every module's models here so they are attached to `Base.metadata`, e.g.:

    from app.modules.users.models import User  # noqa: F401
"""

from app.db.base import Base
from app.modules.admin_users.models import AdminUser
from app.modules.auth.models import AuthToken
from app.modules.locations.models import Area, City
from app.modules.scans.models import PointsEntry, ScanSession, Visit
from app.modules.users.models import User
from app.modules.venue_applications.models import VenueApplication
from app.modules.venues.models import CounterDevice, Venue, VenueMember
from app.modules.whatsapp.models import WhatsAppMessage

__all__ = [
    "AdminUser",
    "Area",
    "AuthToken",
    "Base",
    "City",
    "CounterDevice",
    "PointsEntry",
    "ScanSession",
    "User",
    "Venue",
    "VenueApplication",
    "VenueMember",
    "Visit",
    "WhatsAppMessage",
]
