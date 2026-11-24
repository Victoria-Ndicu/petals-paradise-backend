import html
import logging
import threading
from email.utils import parseaddr

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email

BREVO_TRANSACTIONAL_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"
logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when the configured transactional email API rejects a message."""


def send_transactional_email(subject: str, message: str, from_email: str | None, recipient_list: list[str]) -> int:
    if not recipient_list:
        return 0

    if not settings.BREVO_API_KEY:
        return send_mail(subject, message, from_email, recipient_list, fail_silently=False)

    sender_name, sender_email = parseaddr(from_email or settings.DEFAULT_FROM_EMAIL)
    try:
        validate_email(sender_email)
    except ValidationError as exc:
        raise ValueError("DEFAULT_FROM_EMAIL must contain a valid sender email address.") from exc

    payload = {
        "sender": {"email": sender_email},
        "to": [{"email": recipient} for recipient in recipient_list],
        "subject": subject,
        "textContent": message,
        "htmlContent": f"<pre>{html.escape(message)}</pre>",
    }
    if sender_name:
        payload["sender"]["name"] = sender_name

    try:
        response = requests.post(
            BREVO_TRANSACTIONAL_EMAIL_URL,
            headers={
                "api-key": settings.BREVO_API_KEY,
                "accept": "application/json",
                "content-type": "application/json",
            },
            json=payload,
            timeout=10,
        )
    except requests.RequestException as exc:
        raise EmailDeliveryError("Brevo transactional email request failed.") from exc

    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        detail = response.text[:500]
        raise EmailDeliveryError(
            f"Brevo transactional email failed with HTTP {response.status_code}: {detail}"
        ) from exc

    return len(recipient_list)


def send_transactional_email_async(
    subject: str,
    message: str,
    recipient_list: list[str],
    *,
    context: str,
) -> None:
    if not recipient_list:
        return

    def send() -> None:
        try:
            sent = send_transactional_email(subject, message, None, recipient_list)
            if sent != len(recipient_list):
                raise EmailDeliveryError(f"Only {sent} of {len(recipient_list)} email recipients were accepted.")
        except Exception:
            logger.exception("Transactional email failed for %s", context)

    try:
        threading.Thread(target=send, daemon=True).start()
    except Exception:
        logger.exception("Could not start transactional email for %s", context)
