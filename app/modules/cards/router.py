"""Notch Cards: the customer's own under `/me/cards` (C9, C10) and shared ones under
`/public/cards` (A12)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.common.dependencies import DBSession
from app.common.schemas import CustomResponse
from app.modules.auth.dependencies import CurrentUser, OptionalUser
from app.modules.cards.schemas import CardListItemOut, CardOut, CardUpdateIn, PublicCardOut
from app.modules.cards.service import CardService

router = APIRouter(prefix="/me/cards", tags=["Passport: Notch Cards"])
public_router = APIRouter(prefix="/public/cards", tags=["Website"])


def get_card_service(session: DBSession) -> CardService:
    return CardService(session)


CardServiceDep = Annotated[CardService, Depends(get_card_service)]


@router.get("", response_model=CustomResponse[list[CardListItemOut]], summary="Past cards (C10)")
async def list_cards(
    user: CurrentUser, service: CardServiceDep
) -> CustomResponse[list[CardListItemOut]]:
    return CustomResponse(message="OK", data=await service.list(user))


@router.get(
    "/latest",
    response_model=CustomResponse[CardOut],
    summary="The newest card, for the reveal the 'card ready' message opens (C9)",
)
async def latest(user: CurrentUser, service: CardServiceDep) -> CustomResponse[CardOut]:
    return CustomResponse(message="OK", data=await service.latest(user))


@router.get("/{card_code}", response_model=CustomResponse[CardOut], summary="One card (C9)")
async def get_card(
    card_code: str, user: CurrentUser, service: CardServiceDep
) -> CustomResponse[CardOut]:
    return CustomResponse(message="OK", data=await service.get(user, card_code))


@router.patch(
    "/{card_code}",
    response_model=CustomResponse[CardOut],
    summary="Change look (pick a face), or mark the reveal as seen",
)
async def update_card(
    card_code: str, body: CardUpdateIn, user: CurrentUser, service: CardServiceDep
) -> CustomResponse[CardOut]:
    return CustomResponse(message="Saved.", data=await service.update(user, card_code, body))


@router.post(
    "/{card_code}/share",
    response_model=CustomResponse[CardOut],
    summary="Share: the card's link starts working for anyone. Call before the share sheet",
)
async def share(
    card_code: str, user: CurrentUser, service: CardServiceDep
) -> CustomResponse[CardOut]:
    return CustomResponse(
        message="Your card is shared.", data=await service.set_shared(user, card_code, True)
    )


@router.delete(
    "/{card_code}/share",
    response_model=CustomResponse[CardOut],
    summary="Stop sharing: the link shows 'This card is no longer shared.'",
)
async def unshare(
    card_code: str, user: CurrentUser, service: CardServiceDep
) -> CustomResponse[CardOut]:
    return CustomResponse(
        message="Your card is private again.",
        data=await service.set_shared(user, card_code, False),
    )


@public_router.get(
    "/{card_code}",
    response_model=CustomResponse[PublicCardOut],
    summary="A shared card (A12); 404 'cards.not_shared' once it is private or gone",
)
async def public_card(
    card_code: str, viewer: OptionalUser, service: CardServiceDep
) -> CustomResponse[PublicCardOut]:
    return CustomResponse(message="OK", data=await service.public(card_code, viewer))
