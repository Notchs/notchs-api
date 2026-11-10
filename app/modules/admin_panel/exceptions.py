from app.common.exceptions import ConflictException, NotFoundException, UnprocessableException


class AdminVenueNotFoundException(NotFoundException):
    error_code = "admin.venue_not_found"
    message = "Venue not found."


class AdminNotchNotFoundException(NotFoundException):
    error_code = "admin.notch_not_found"
    message = "Notch not found."


class AdminCustomerNotFoundException(NotFoundException):
    error_code = "admin.customer_not_found"
    message = "Customer not found."


class AdminStatusChangeNotAllowedException(ConflictException):
    error_code = "admin.status_change_not_allowed"
    message = "That status change isn't allowed."


class AdminCounterNotPairedException(ConflictException):
    error_code = "admin.counter_not_paired"
    message = "Pair the venue's counter phone first; pairing puts the venue live."


class AdminDeleteNotConfirmedException(UnprocessableException):
    error_code = "admin.delete_not_confirmed"
    message = "Type the customer's handle exactly to confirm."
