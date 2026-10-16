from app.clients.email import EmailMessage
from app.modules.venue_applications.models import VenueApplication
from app.modules.venues.dropdowns import CATEGORY_LABELS
from app.modules.venues.models import VenueCategory


def application_rejected_email(*, application: VenueApplication) -> EmailMessage:
    reason = (
        f"\n\nThe reason we gave: {application.rejection_reason}"
        if application.rejection_reason
        else ""
    )
    return EmailMessage(
        to=application.email,
        subject=f"Your Notchs application for {application.venue_name}",
        text=(
            f"Hi {application.contact_name},\n\n"
            f"Thank you for applying to bring {application.venue_name} to Notchs. "
            f"We can't approve it right now.{reason}\n\n"
            "You're welcome to apply again later.\n\n— Team Notchs"
        ),
    )


def new_application_email(*, to: str, application: VenueApplication, city: str) -> EmailMessage:
    """Heads-up for the team. No applicant phone or email in it: those stay in the admin panel."""
    category = CATEGORY_LABELS[VenueCategory(application.category)]
    return EmailMessage(
        to=to,
        subject=f"New venue application: {application.venue_name} ({city})",
        text=(
            f"{application.venue_name} ({category}, {city}) applied to join Notchs.\n\n"
            f"Application: {application.application_code}\n"
            f"Map: {application.google_maps_url}\n\n"
            "Review it in the admin panel."
        ),
    )
