from django.conf import settings

from apps.core.email import send_transactional_email_async

from .models import Inquiry


def notify_inquiry_received(inquiry: Inquiry) -> None:
    occasion = inquiry.get_occasion_display()
    admin_message = "\n".join(
        [
            f"New inquiry {inquiry.pk}",
            "",
            f"Name: {inquiry.name}",
            f"Email: {inquiry.email}",
            f"Phone: {inquiry.phone}",
            f"Occasion: {occasion}",
            f"Preferred follow-up: {inquiry.get_preferred_contact_display()}",
            "",
            "Details:",
            inquiry.details or "(No additional details provided.)",
        ]
    )
    admin_recipients = settings.INQUIRY_ALERT_EMAILS or settings.ORDER_ALERT_EMAILS
    if admin_recipients:
        send_transactional_email_async(
            f"New Petals Paradise inquiry: {occasion}",
            admin_message,
            admin_recipients,
            context=f"staff inquiry alert {inquiry.pk}",
        )

    message = (
        f"Hello {inquiry.name},\n\n"
        f"Thank you for contacting Petals Paradise about {occasion.lower()}. "
        "We have received your inquiry and will be in touch soon.\n\n"
        f"Your inquiry reference is {inquiry.pk}."
    )
    send_transactional_email_async(
        "We received your Petals Paradise inquiry",
        message,
        [inquiry.email],
        context=f"inquiry acknowledgement {inquiry.pk}",
    )
