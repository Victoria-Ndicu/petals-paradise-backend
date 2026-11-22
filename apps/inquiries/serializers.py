from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.core.phone import normalize_ke_phone

from .models import Inquiry

User = get_user_model()


class InquiryCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    phone = serializers.CharField(max_length=20)
    occasion = serializers.ChoiceField(choices=Inquiry.Occasion.choices)
    preferred_contact = serializers.ChoiceField(choices=Inquiry.Channel.choices, required=False, default="whatsapp")
    details = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")
    # Honeypot: real people never see/fill this field; bots usually do.
    website = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = dict(data)
            if "preferredContact" in data:
                data.setdefault("preferred_contact", data.pop("preferredContact"))
        try:
            return super().to_internal_value(data)
        except serializers.ValidationError as exc:
            detail = exc.detail
            if isinstance(detail, dict) and "preferred_contact" in detail:
                detail["preferredContact"] = detail.pop("preferred_contact")
            raise serializers.ValidationError(detail)

    def validate_name(self, value):
        value = value.strip()
        if len(value) < 2:
            raise serializers.ValidationError("Enter your name.")
        return value

    def validate_phone(self, value):
        phone = normalize_ke_phone(value)
        if not phone:
            raise serializers.ValidationError("Enter a valid Kenyan phone number.")
        return phone

    def validate_details(self, value):
        return value.strip()


class InquiryReadSerializer(serializers.ModelSerializer):
    occasion_label = serializers.CharField(source="get_occasion_display", read_only=True)
    assigned_to_name = serializers.CharField(source="assigned_to.name", read_only=True, default=None)

    class Meta:
        model = Inquiry
        fields = [
            "id",
            "name",
            "phone",
            "occasion",
            "occasion_label",
            "preferred_contact",
            "details",
            "status",
            "assigned_to",
            "assigned_to_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "name", "phone", "occasion", "preferred_contact", "details", "created_at", "updated_at"]

    def validate_assigned_to(self, value):
        if value is not None and not value.is_staff:
            raise serializers.ValidationError("Inquiries can only be assigned to staff.")
        return value
