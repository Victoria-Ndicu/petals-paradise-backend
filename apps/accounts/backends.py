from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from apps.core.phone import normalize_ke_phone


class PhoneOrEmailBackend(ModelBackend):
    """Authenticate with a Kenyan phone number or an email address."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        identifier = (username or kwargs.get(User.USERNAME_FIELD) or "").strip()
        if not identifier or not password:
            return None

        user = None
        if "@" in identifier:
            user = User.objects.filter(email=identifier.lower()).first()
        else:
            phone = normalize_ke_phone(identifier)
            if phone:
                user = User.objects.filter(phone=phone).first()

        if user is None:
            # Burn comparable CPU time so response timing doesn't reveal accounts.
            User().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
