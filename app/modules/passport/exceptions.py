from app.common.exceptions import UnprocessableException


class PassportAreaInvalidException(UnprocessableException):
    error_code = "passport.area_invalid"
    message = "Pick an area from the list."
