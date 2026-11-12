"""Model registry for Alembic autogenerate.

Import every module's models here so they are attached to `Base.metadata`, e.g.:

    from app.modules.users.models import User  # noqa: F401
"""

from app.db.base import Base
from app.modules.admin_users.models import AdminRoleDefinition, AdminRolePermission, AdminUser
from app.modules.audit.models import AuditLog
from app.modules.auth.models import AuthToken
from app.modules.claims.models import VisitClaim
from app.modules.consents.models import Consent
from app.modules.feedback.models import VisitFeedback
from app.modules.locations.models import Area, City
from app.modules.referrals.models import Referral, StarPointsEntry
from app.modules.scans.models import PointsEntry, ScanSession, Visit
from app.modules.tags.models import Tag, VenueItem, VenueTag, VisitTag
from app.modules.users.models import User
from app.modules.venue_applications.models import VenueApplication
from app.modules.venues.models import CounterDevice, Venue, VenueMember, VenuePhoto
from app.modules.whatsapp.models import WhatsAppMessage

__all__ = [
    "AdminRoleDefinition",
    "AdminRolePermission",
    "AdminUser",
    "Area",
    "AuditLog",
    "AuthToken",
    "Base",
    "City",
    "Consent",
    "CounterDevice",
    "PointsEntry",
    "Referral",
    "ScanSession",
    "StarPointsEntry",
    "Tag",
    "User",
    "Venue",
    "VenueApplication",
    "VenueItem",
    "VenueMember",
    "VenuePhoto",
    "VenueTag",
    "Visit",
    "VisitClaim",
    "VisitFeedback",
    "VisitTag",
    "WhatsAppMessage",
]
