from app.common.exceptions import BadRequestException, NotFoundException


class CardNotFoundException(NotFoundException):
    error_code = "cards.not_found"
    message = "Card not found."


class CardNotSharedException(NotFoundException):
    # Same answer for a card that never existed, was made private, or whose owner left.
    error_code = "cards.not_shared"
    message = "This card is no longer shared."


class CardFaceUnavailableException(BadRequestException):
    error_code = "cards.face_unavailable"
    message = "This card has no such face. A rank face needs at least 3 Notchs."
