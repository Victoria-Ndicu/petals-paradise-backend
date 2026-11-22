from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """Append-only record of sensitive administrative and state changes."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=80, db_index=True)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=64)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} {self.object_type}:{self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("AuditEvent is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditEvent is append-only.")


def record_audit(actor, action: str, obj, **detail) -> AuditEvent:
    return AuditEvent.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        object_type=obj.__class__.__name__,
        object_id=str(obj.pk),
        detail=detail,
    )


class Notification(models.Model):
    """Outbound customer/staff message. Status only becomes SENT when a provider
    confirms acceptance; until a provider is configured messages stay QUEUED."""

    class Channel(models.TextChoices):
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"
        EMAIL = "email", "Email"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    channel = models.CharField(max_length=10, choices=Channel.choices)
    kind = models.CharField(max_length=40, db_index=True)  # e.g. order_confirmed
    recipient = models.CharField(max_length=254)
    related_type = models.CharField(max_length=40, blank=True)
    related_id = models.CharField(max_length=64, blank=True)
    dedupe_key = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    provider_message_id = models.CharField(max_length=120, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


def enqueue_notification(*, channel, kind, recipient, related_type="", related_id="") -> "Notification":
    """Idempotent: the same (kind, related object, channel) is queued only once."""
    dedupe_key = f"{kind}:{related_type}:{related_id}:{channel}"
    obj, _ = Notification.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults=dict(
            channel=channel, kind=kind, recipient=recipient, related_type=related_type, related_id=str(related_id)
        ),
    )
    return obj
