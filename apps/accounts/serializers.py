from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.core.phone import normalize_ke_phone

User = get_user_model()


def _clean_phone(value: str) -> str:
    phone = normalize_ke_phone(value)
    if not phone:
        raise serializers.ValidationError("Enter a valid Kenyan phone number.")
    return phone


class ProfileSerializer(serializers.ModelSerializer):
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    socialProviders = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "name", "phone", "email", "default_delivery_area", "default_address", "socialProviders"]
        read_only_fields = ["id", "socialProviders"]

    def get_socialProviders(self, obj):
        if not obj.pk:
            return []
        return sorted(obj.social_accounts.values_list("provider", flat=True))

    def validate_name(self, value):
        value = value.strip()
        if len(value) < 2:
            raise serializers.ValidationError("Enter your name.")
        return value

    def validate_phone(self, value):
        phone = _clean_phone(value)
        qs = User.objects.filter(phone=phone)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("An account with this phone number already exists.")
        return phone

    def validate_email(self, value):
        value = (value or "").strip().lower()
        if not value:
            return None
        qs = User.objects.filter(email=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_default_delivery_area(self, value):
        if value:
            from apps.catalogue.models import DeliveryOption

            if not DeliveryOption.objects.filter(slug=value, is_active=True).exists():
                raise serializers.ValidationError("Choose a supported delivery area.")
        return value


class SignupSerializer(ProfileSerializer):
    password = serializers.CharField(write_only=True, min_length=8, max_length=128, trim_whitespace=False)

    class Meta(ProfileSerializer.Meta):
        fields = ProfileSerializer.Meta.fields + ["password"]

    def validate(self, attrs):
        candidate = User(name=attrs.get("name", ""), phone=attrs.get("phone", ""), email=attrs.get("email"))
        try:
            validate_password(attrs["password"], candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField(max_length=254)
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class SocialLoginSerializer(serializers.Serializer):
    idToken = serializers.CharField(max_length=8192, trim_whitespace=True)
    nonce = serializers.CharField(max_length=256, required=False, allow_blank=True)
    # Apple only sends the user's name to the browser (not in the token), so the SPA passes it on.
    name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    # Required the first time someone signs up (phone is our unique customer key).
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)


class SocialLinkSerializer(serializers.Serializer):
    idToken = serializers.CharField(max_length=8192, trim_whitespace=True)
    nonce = serializers.CharField(max_length=256, required=False, allow_blank=True)
