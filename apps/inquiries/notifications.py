from apps.core.email import send_transactional_email_async

from .models import Inquiry


def notify_inquiry_received(inquiry: Inquiry) -> None:
    occasion = inquiry.get_occasion_display()
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
