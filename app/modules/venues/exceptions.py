from app.core.exceptions import BadRequestException, ConflictException, NotFoundException


class VenueNotFoundException(NotFoundException):
    error_code = "venues.not_found"
    message = "Venue not found."


class VenueSlugTakenException(ConflictException):
    error_code = "venues.slug_taken"
    message = "Another venue in this city already uses this slug."


class FoundingVenueNoTakenException(ConflictException):
    error_code = "venues.founding_venue_no_taken"
    message = "This founding venue number is already assigned."


class VenueLocationInvalidException(BadRequestException):
    error_code = "venues.location_invalid"
    message = "The city or area does not exist, or the area is not in this city."
