"""`/me`: the customer's passport (C1-C14). Logged in with the WhatsApp link (`nx_user_*`)."""

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.common.dependencies import DBSession, RedisClient
from app.common.schemas import CustomResponse
from app.modules.auth.cookies import clear_session_cookies
from app.modules.auth.dependencies import CurrentUser
from app.modules.auth.enums import SubjectType
from app.modules.passport.schemas import (
    BadgesOut,
    DeleteMeIn,
    HistoryOut,
    HomeOut,
    LeaderboardOut,
    MeOut,
    MeUpdate,
    NotchOut,
    PassportVenueOut,
    PlacesOut,
    ReportIn,
    YearOut,
)
from app.modules.passport.schemas import Period as PeriodName
from app.modules.passport.service import PassportService
from app.modules.users.deletion import DeletionService
from app.modules.users.models import DeletionSource
from app.modules.venues.models import VenueCategory

router = APIRouter(prefix="/me", tags=["Passport"])


def get_passport_service(session: DBSession, redis: RedisClient) -> PassportService:
    return PassportService(session, redis)


PassportDep = Annotated[PassportService, Depends(get_passport_service)]


@router.get("", response_model=CustomResponse[MeOut], summary="The customer (C1, C11)")
async def me(user: CurrentUser, passport: PassportDep) -> CustomResponse[MeOut]:
    return CustomResponse(message="OK", data=await passport.me(user))


@router.patch(
    "",
    response_model=CustomResponse[MeOut],
    summary="Claim (name, area) and settings (theme, public profile)",
)
async def update_me(
    body: MeUpdate, user: CurrentUser, passport: PassportDep
) -> CustomResponse[MeOut]:
    return CustomResponse(message="Saved.", data=await passport.update_me(user, body))


@router.get("/home", response_model=CustomResponse[HomeOut], summary="Home (C2)")
async def home(user: CurrentUser, passport: PassportDep) -> CustomResponse[HomeOut]:
    return CustomResponse(message="OK", data=await passport.home(user))


@router.get(
    "/notches", response_model=CustomResponse[HistoryOut], summary="My Notchs by month (C3)"
)
async def history(
    user: CurrentUser,
    passport: PassportDep,
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$", examples=["2026-10"]),
) -> CustomResponse[HistoryOut]:
    return CustomResponse(message="OK", data=await passport.history(user, month))


@router.get(
    "/notches/{notch_code}", response_model=CustomResponse[NotchOut], summary="Notch detail (C4)"
)
async def notch(
    notch_code: str, user: CurrentUser, passport: PassportDep
) -> CustomResponse[NotchOut]:
    return CustomResponse(message="OK", data=await passport.notch(user, notch_code))


@router.post(
    "/notches/{notch_code}/report",
    response_model=CustomResponse[NotchOut],
    summary="Report a mistake: the Notch goes to the review queue (C4)",
)
async def report(
    notch_code: str, body: ReportIn, user: CurrentUser, passport: PassportDep
) -> CustomResponse[NotchOut]:
    return CustomResponse(
        message="Thanks. We'll look at it.",
        data=await passport.report(user, notch_code, body.message),
    )


@router.get("/badges", response_model=CustomResponse[BadgesOut], summary="Badges (C8)")
async def badges(user: CurrentUser, passport: PassportDep) -> CustomResponse[BadgesOut]:
    return CustomResponse(message="OK", data=await passport.badges(user))


@router.get(
    "/leaderboard", response_model=CustomResponse[LeaderboardOut], summary="Leaderboard (C7)"
)
async def leaderboard(
    user: CurrentUser,
    passport: PassportDep,
    period: PeriodName = "month",
    category: VenueCategory | None = None,
    area_id: uuid.UUID | None = None,
) -> CustomResponse[LeaderboardOut]:
    return CustomResponse(
        message="OK",
        data=await passport.leaderboard(user, period, category=category, area_id=area_id),
    )


@router.get(
    "/places",
    response_model=CustomResponse[PlacesOut],
    summary="Places near you, or by area without a location (C5)",
)
async def places(
    user: CurrentUser,
    passport: PassportDep,
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    area_id: uuid.UUID | None = None,
    category: VenueCategory | None = None,
    open_now: bool = False,
) -> CustomResponse[PlacesOut]:
    return CustomResponse(
        message="OK",
        data=await passport.places(
            user, lat=lat, lng=lng, area_id=area_id, category=category, open_now=open_now
        ),
    )


@router.get(
    "/venues/{venue_code}",
    response_model=CustomResponse[PassportVenueOut],
    summary="A venue with your visits on top (C6)",
)
async def venue(
    venue_code: str, user: CurrentUser, passport: PassportDep
) -> CustomResponse[PassportVenueOut]:
    return CustomResponse(message="OK", data=await passport.venue(user, venue_code))


@router.get(
    "/year/{year}", response_model=CustomResponse[YearOut], summary="The year in Notchs (C14)"
)
async def year(
    year: Annotated[int, Path(ge=2026, le=2100)],
    user: CurrentUser,
    passport: PassportDep,
) -> CustomResponse[YearOut]:
    return CustomResponse(message="OK", data=await passport.year(user, year))


@router.get(
    "/export",
    summary="Download my data (C12): a JSON file with everything Notchs keeps about you",
    response_class=Response,
)
async def export(user: CurrentUser, passport: PassportDep) -> Response:
    data = await passport.export(user)
    # The file holds the customer's own phone number: it is theirs, but never cache it.
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="notchs-{user.user_code}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/delete",
    status_code=status.HTTP_200_OK,
    response_model=CustomResponse[None],
    summary="Delete everything (C12). Logs out at once; the data is wiped within a minute",
)
async def delete_me(
    body: DeleteMeIn,
    user: CurrentUser,
    response: Response,
    session: DBSession,
) -> CustomResponse[None]:
    await DeletionService(session).delete_now(user, DeletionSource.APP)
    clear_session_cookies(response, SubjectType.USER)
    return CustomResponse(message="Everything is deleted.")
