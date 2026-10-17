from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
    TooManyRequestsException,
    UnauthorizedException,
)


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


class CounterNotFoundException(NotFoundException):
    error_code = "counters.not_found"
    message = "This venue has no counter phone yet."


class DeviceNotPairedException(UnauthorizedException):
    error_code = "devices.not_paired"
    message = "This phone is not paired with a venue. Ask the venue owner for a pairing link."


class CounterRateLimitedException(TooManyRequestsException):
    error_code = "counters.rate_limited"
    message = "Too many pairing links. Please try again in an hour."
