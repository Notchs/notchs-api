from app.common.exceptions import NotFoundException


class NotchReviewNotFoundException(NotFoundException):
    error_code = "notches.not_found"
    message = "Notch not found."


class SignalNotFoundException(NotFoundException):
    error_code = "fraud_signals.not_found"
    message = "Fraud signal not found."
