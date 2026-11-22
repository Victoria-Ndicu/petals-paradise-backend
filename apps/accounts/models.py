from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.phone import normalize_ke_phone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, phone, password, **extra):
        normalized = normalize_ke_phone(phone)
        if not normalized:
            raise ValueError("A valid Kenyan phone number is required.")
        email = (extra.pop("email", None) or "").strip().lower() or None
        user = self.model(phone=normalized, email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(phone, password, **extra)

    def create_superuser(self, phone, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("name", "Administrator")
        return self._create(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Customer or staff account. Staff flags can only be set from the admin/CLI,
    never through the public sign-up API."""

    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=15, unique=True, help_text="Normalized, e.g. 254712345678")
    email = models.EmailField(max_length=254, unique=True, null=True, blank=True)
    default_delivery_area = models.CharField(max_length=80, blank=True)
    default_address = models.CharField(max_length=300, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["name"]

    def __str__(self):
        return f"{self.name} ({self.phone})"

    def save(self, *args, **kwargs):
        if self.email is not None:
            self.email = self.email.strip().lower() or None
        super().save(*args, **kwargs)


class SocialAccount(models.Model):
    """Link between a customer and an identity at Google / Apple.

    Matched on the provider's stable `sub` (subject) id, never on email."""

    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"
        APPLE = "apple", "Apple"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="social_accounts")
    provider = models.CharField(max_length=20, choices=Provider.choices)
    subject = models.CharField(max_length=255)
    email = models.EmailField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "subject"], name="uniq_social_provider_subject"),
            models.UniqueConstraint(fields=["user", "provider"], name="uniq_social_user_provider"),
        ]

    def __str__(self):
        return f"{self.provider}:{self.subject}"
