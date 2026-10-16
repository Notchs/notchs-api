"""Reviewing applications in the admin panel: list, view, approve, reject.

Approval creates the venue (status `signed`) and its owner login in one transaction, then
returns the "set your password" email for the router to send.
"""

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.email import EmailMessage
from app.common.dependencies import PaginationParams
from app.modules.admin_users.models import AdminUser
from app.modules.auth.venue_auth import VenueAuthService
from app.modules.locations.models import Area
from app.modules.venue_applications import messages
from app.modules.venue_applications.exceptions import (
    ApplicationAreaInvalidException,
    ApplicationDuplicateException,
    ApplicationInvalidStateException,
    ApplicationNotFoundException,
)
from app.modules.venue_applications.models import ApplicationStatus, VenueApplication
from app.modules.venue_applications.schemas import ApproveIn, RejectIn
from app.modules.venues.models import Venue, VenueMember, VenueMemberRole, VenueStatus
from app.modules.venues.repository import VenueRepository
from app.modules.venues.schemas import VenueCreate
from app.modules.venues.service import VenueService

_CODE = re.compile(r"APP-[0-9A-Z]{8}")


class ApplicationReviewService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis

    async def list(
        self, params: PaginationParams, *, status: ApplicationStatus | None
    ) -> tuple[Sequence[VenueApplication], int]:
        """Oldest first, so the queue is worked through in order."""
        where = [VenueApplication.status == status] if status else []
        items = await self.session.scalars(
            select(VenueApplication)
            .where(*where)
            .order_by(VenueApplication.created_at, VenueApplication.id)
            .offset(params.offset)
            .limit(params.size)
        )
        total = await self.session.scalar(
            select(func.count()).select_from(VenueApplication).where(*where)
        )
        return items.all(), total or 0

    async def get(self, ref: str, *, for_update: bool = False) -> VenueApplication:
        """By id or by public code (`APP-7K3M9QX2`, any case)."""
        ref = ref.strip()
        if _CODE.fullmatch(ref.upper()):
            condition = VenueApplication.application_code == ref.upper()
        else:
            try:
                condition = VenueApplication.id == uuid.UUID(ref)
            except ValueError as exc:
                raise ApplicationNotFoundException() from exc
        query = select(VenueApplication).where(condition)
        application = await self.session.scalar(query.with_for_update() if for_update else query)
        if application is None:
            raise ApplicationNotFoundException()
        return application

    async def approve(
        self, ref: str, admin: AdminUser, body: ApproveIn
    ) -> tuple[VenueApplication, Venue, VenueMember, EmailMessage]:
        application = await self._pending(ref)
        if body.area_id is not None:
            area = await self.session.get(Area, body.area_id)
            if area is None or area.city_id != application.city_id:
                raise ApplicationAreaInvalidException()
        if await self.session.scalar(
            select(exists().where(VenueMember.email == application.email))
        ):
            raise ApplicationDuplicateException()

        latitude, longitude = (
            (body.latitude, body.longitude)
            if body.latitude is not None
            else (application.latitude, application.longitude)
        )
        venues = VenueService(VenueRepository(self.session))
        venue = await venues.create(
            VenueCreate(
                city_id=application.city_id,
                area_id=body.area_id,
                name=application.venue_name,
                category=application.category,
                address=body.address,
                landmark=application.landmark,
                latitude=latitude,
                longitude=longitude,
            )
        )
        venue = await venues.set_status(venue.id, VenueStatus.SIGNED, admin_id=admin.id)

        member = VenueMember(
            venue_id=venue.id,
            phone_e164=application.phone_e164,
            name=application.contact_name,
            email=application.email,
            role=VenueMemberRole.OWNER,
        )
        self.session.add(member)
        try:
            # The unique email on venue_members is the final guard against a race.
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise ApplicationDuplicateException() from exc

        self._mark_reviewed(application, admin, ApplicationStatus.APPROVED)
        application.venue_id = venue.id
        await self.session.flush()
        await self.session.refresh(application)  # updated_at is set by the database
        welcome = await VenueAuthService(self.session, self.redis).welcome_email(member, venue)
        return application, venue, member, welcome

    async def reject(
        self, ref: str, admin: AdminUser, body: RejectIn
    ) -> tuple[VenueApplication, EmailMessage | None]:
        application = await self._pending(ref)
        application.rejection_reason = body.reason or None
        self._mark_reviewed(application, admin, ApplicationStatus.REJECTED)
        await self.session.flush()
        await self.session.refresh(application)  # updated_at is set by the database
        notice = (
            messages.application_rejected_email(application=application) if body.notify else None
        )
        return application, notice

    async def _pending(self, ref: str) -> VenueApplication:
        application = await self.get(ref, for_update=True)
        if application.status != ApplicationStatus.PENDING:
            raise ApplicationInvalidStateException()
        return application

    @staticmethod
    def _mark_reviewed(
        application: VenueApplication, admin: AdminUser, status: ApplicationStatus
    ) -> None:
        application.status = status
        application.reviewed_by_admin_id = admin.id
        application.reviewed_at = datetime.now(UTC)
