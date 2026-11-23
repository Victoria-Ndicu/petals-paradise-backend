import logging
import smtplib

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.mail import send_mail
from rest_framework import status

from apps.core.exceptions import ApiError

TOKEN_SALT = "accounts.email-verification"
logger = logging.getLogger(__name__)
User = get_user_model()


def send_verification_email(user):
    token = signing.dumps(
        {"user_id": user.pk, "email": user.email},
        salt=TOKEN_SALT,
        compress=True,
    )
    verification_url = (
        f"{settings.FRONTEND_URL.rstrip('/')}/#/verify-email?token={token}"
    )
    try:
        sent = send_mail(
            subject="Verify your Petals Paradise email",
            message=(
                f"Hello {user.name},\n\n"
                "Please verify your email address by opening this link:\n"
                f"{verification_url}\n\n"
                "This link expires in 24 hours. If you did not create this account, "
                "you can ignore this email."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        logger.exception("Could not send email verification to user %s", user.pk)
        raise ApiError(
            "EMAIL_DELIVERY_FAILED",
            "We couldn't send the verification email. Sign in and request a new one from your account.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    if sent != 1:
        raise ApiError(
            "EMAIL_DELIVERY_FAILED",
            "We couldn't send the verification email. Sign in and request a new one from your account.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )


def verify_email_token(token):
    try:
        payload = signing.loads(
            token,
            salt=TOKEN_SALT,
            max_age=settings.EMAIL_VERIFICATION_TIMEOUT_SECONDS,
        )
    except signing.SignatureExpired as exc:
        raise ApiError(
            "EMAIL_VERIFICATION_EXPIRED",
            "This verification link has expired. Request a new one.",
            status.HTTP_400_BAD_REQUEST,
        ) from exc
    except signing.BadSignature as exc:
        raise ApiError(
            "INVALID_EMAIL_VERIFICATION",
            "This verification link is invalid. Request a new one.",
            status.HTTP_400_BAD_REQUEST,
        ) from exc

    user = User.objects.filter(
        pk=payload.get("user_id"),
        email=payload.get("email"),
    ).first()
    if not user:
        raise ApiError(
            "INVALID_EMAIL_VERIFICATION",
            "This verification link is invalid. Request a new one.",
            status.HTTP_400_BAD_REQUEST,
        )
    if not user.email_verified:
        user.email_verified = True
        user.save(update_fields=["email_verified", "updated_at"])
    return user
