from django.contrib.auth import authenticate, get_user_model, login, logout
from django.db import IntegrityError, transaction
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.exceptions import ApiError
from apps.core.phone import normalize_ke_phone

from .models import SocialAccount
from .email_verification import send_verification_email, verify_email_token
from .serializers import (
    EmailVerificationResendSerializer,
    EmailVerificationSerializer,
    LoginSerializer,
    ProfileSerializer,
    SignupSerializer,
    SocialLinkSerializer,
    SocialLoginSerializer,
)
from .social import PROVIDERS, verify_id_token

User = get_user_model()
SESSION_BACKEND = "apps.accounts.backends.PhoneOrEmailBackend"


class CsrfView(APIView):
    """Sets the csrftoken cookie; the SPA echoes it in the X-CSRFToken header."""

    permission_classes = [AllowAny]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"csrfToken": get_token(request)})


@method_decorator(csrf_protect, name="dispatch")
class SignupView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "signup"

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        send_verification_email(user)
        login(request, user, backend="apps.accounts.backends.PhoneOrEmailBackend")
        return Response(ProfileSerializer(user).data, status=status.HTTP_201_CREATED)


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            username=serializer.validated_data["identifier"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            # Generic message: never reveal whether the account exists.
            raise ApiError("INVALID_CREDENTIALS", "Incorrect phone/email or password.", status.HTTP_401_UNAUTHORIZED)
        login(request, user)
        return Response(ProfileSerializer(user).data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)  # flushes the server-side session
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(ProfileSerializer(request.user).data)

    def patch(self, request):
        serializer = ProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        previous_email = request.user.email
        serializer.save()
        if request.user.email != previous_email:
            request.user.email_verified = False
            request.user.save(update_fields=["email_verified", "updated_at"])
            if request.user.email:
                send_verification_email(request.user)
        return Response(serializer.data)


@method_decorator(csrf_protect, name="dispatch")
class VerifyEmailView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = EmailVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = verify_email_token(serializer.validated_data["token"])
        return Response({"email": user.email, "email_verified": user.email_verified})


@method_decorator(csrf_protect, name="dispatch")
class ResendEmailVerificationView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = EmailVerificationResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        user = User.objects.filter(email=email, email_verified=False).first()
        if user:
            send_verification_email(user)
        return Response(
            {"message": "If that address has an unverified account, a verification email has been sent."}
        )


def _provider_or_404(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ApiError("NOT_FOUND", "Unknown sign-in provider.", status.HTTP_404_NOT_FOUND)
    return provider


@method_decorator(csrf_protect, name="dispatch")
class SocialLoginView(APIView):
    """Sign in or sign up with a Google/Apple ID token.

    - Known provider identity  -> sign in.
    - New identity             -> create a customer and sign in; phone is optional.
    - A verified provider email matching an existing account links that identity.
      An unverified provider email never links or claims an existing account.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request, provider):
        provider = _provider_or_404(provider)
        serializer = SocialLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        identity = verify_id_token(provider, data["idToken"], data.get("nonce") or None)

        link = SocialAccount.objects.select_related("user").filter(provider=provider, subject=identity.subject).first()
        if link:
            if not link.user.is_active:
                raise ApiError("ACCOUNT_DISABLED", "This account is disabled.", status.HTTP_403_FORBIDDEN)
            login(request, link.user, backend=SESSION_BACKEND)
            return Response(ProfileSerializer(link.user).data)

        # --- first time: register ---
        raw_phone = (data.get("phone") or "").strip()
        phone = normalize_ke_phone(raw_phone) if raw_phone else None
        if raw_phone and not phone:
            raise ApiError(
                "VALIDATION_ERROR",
                "Please review the highlighted fields.",
                fields={"phone": "Enter a valid Kenyan phone number."},
            )
        if phone and User.objects.filter(phone=phone).exists():
            raise ApiError(
                "VALIDATION_ERROR",
                "Please review the highlighted fields.",
                fields={"phone": "An account with this phone number already exists."},
            )
        matched_user = None
        if identity.claimed_email:
            matched_user = User.objects.filter(email=identity.claimed_email).first()
        if matched_user:
            if not identity.email_verified:
                raise ApiError(
                    "EMAIL_NOT_VERIFIED",
                    "Verify this email with your sign-in provider, then try again.",
                    status.HTTP_409_CONFLICT,
                )
            if not matched_user.is_active:
                raise ApiError("ACCOUNT_DISABLED", "This account is disabled.", status.HTTP_403_FORBIDDEN)
            if matched_user.social_accounts.filter(provider=provider).exists():
                raise ApiError(
                    "SOCIAL_ALREADY_LINKED",
                    f"A different {PROVIDERS[provider]['label']} account is already connected.",
                    status.HTTP_409_CONFLICT,
                )
            try:
                with transaction.atomic():
                    SocialAccount.objects.create(
                        user=matched_user,
                        provider=provider,
                        subject=identity.subject,
                        email=identity.email,
                    )
                    if not matched_user.email_verified:
                        matched_user.email_verified = True
                        matched_user.save(update_fields=["email_verified", "updated_at"])
            except IntegrityError as exc:
                raise ApiError(
                    "SOCIAL_ACCOUNT_IN_USE",
                    "That social account is already connected to another account.",
                    status.HTTP_409_CONFLICT,
                ) from exc
            login(request, matched_user, backend=SESSION_BACKEND)
            return Response(ProfileSerializer(matched_user).data)

        name = (data.get("name") or identity.name or "").strip() or "Customer"
        try:
            with transaction.atomic():
                user = User(
                    name=name[:120],
                    phone=phone,
                    email=identity.email,
                    email_verified=identity.email_verified,
                )
                user.set_unusable_password()  # social-only: no password sign-in until they set one
                user.save()
                SocialAccount.objects.create(
                    user=user, provider=provider, subject=identity.subject, email=identity.email or ""
                )
        except IntegrityError:
            raise ApiError("ACCOUNT_EXISTS", "An account with these details already exists.", status.HTTP_409_CONFLICT)

        login(request, user, backend=SESSION_BACKEND)
        return Response(ProfileSerializer(user).data, status=status.HTTP_201_CREATED)


@method_decorator(csrf_protect, name="dispatch")
class SocialLinkView(APIView):
    """Signed-in customer connects a Google/Apple identity to their existing account."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request, provider):
        provider = _provider_or_404(provider)
        serializer = SocialLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        identity = verify_id_token(provider, data["idToken"], data.get("nonce") or None)

        existing = SocialAccount.objects.filter(provider=provider, subject=identity.subject).first()
        if existing:
            if existing.user_id == request.user.pk:
                return Response(ProfileSerializer(request.user).data)
            raise ApiError(
                "SOCIAL_ACCOUNT_IN_USE",
                "That account is already connected to another customer.",
                status.HTTP_409_CONFLICT,
            )
        if request.user.social_accounts.filter(provider=provider).exists():
            raise ApiError(
                "SOCIAL_ALREADY_LINKED",
                f"A different {PROVIDERS[provider]['label']} account is already connected.",
                status.HTTP_409_CONFLICT,
            )
        try:
            SocialAccount.objects.create(
                user=request.user, provider=provider, subject=identity.subject, email=identity.email or ""
            )
        except IntegrityError:
            raise ApiError("SOCIAL_ACCOUNT_IN_USE", "That account is already connected.", status.HTTP_409_CONFLICT)
        return Response(ProfileSerializer(request.user).data)
