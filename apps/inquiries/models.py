import uuid

from django.conf import settings
from django.db import models


class Inquiry(models.Model):
    class Occasion(models.TextChoices):
        FLOWER_DELIVERY = "flower_delivery", "Flower delivery"
        WEDDING = "wedding", "Wedding"
        EVENT_DECOR = "event_decor", "Event décor"
        FLOWER_MAINTENANCE = "flower_maintenance", "Flower maintenance"
        FUNERAL = "funeral", "Funeral / sympathy flowers"

    class Channel(models.TextChoices):
        PHONE = "phone", "Phone call"
        WHATSAPP = "whatsapp", "WhatsApp"
        SMS = "sms", "SMS"
        EMAIL = "email", "Email"

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        QUOTED = "quoted", "Quoted"
        CLOSED = "closed", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=15)
    email = models.EmailField(max_length=254, blank=True, null=True)
    occasion = models.CharField(max_length=30, choices=Occasion.choices)
    preferred_contact = models.CharField(max_length=10, choices=Channel.choices, default=Channel.WHATSAPP)
    details = models.TextField(blank=True, max_length=2000)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_inquiries",
        limit_choices_to={"is_staff": True},
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "inquiries"

    def __str__(self):
        return f"{self.name} - {self.get_occasion_display()}"
