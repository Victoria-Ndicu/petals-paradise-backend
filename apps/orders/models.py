import uuid

from django.conf import settings
from django.db import models


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "Pending payment"
        CONFIRMED = "confirmed", "Confirmed"
        PREPARING = "preparing", "Preparing"
        READY = "ready", "Ready"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    # Allowed transitions. Anything not listed here is rejected by the server.
    TRANSITIONS = {
        Status.PENDING_PAYMENT: {Status.CONFIRMED, Status.CANCELLED},
        Status.CONFIRMED: {Status.PREPARING, Status.CANCELLED},
        Status.PREPARING: {Status.READY, Status.CANCELLED},
        Status.READY: {Status.OUT_FOR_DELIVERY, Status.CANCELLED},
        Status.OUT_FOR_DELIVERY: {Status.DELIVERED},
        Status.DELIVERED: set(),
        Status.CANCELLED: set(),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=20, unique=True, editable=False)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT, db_index=True)

    # Recipient / delivery snapshot (copied at purchase so later edits don't rewrite history).
    recipient_name = models.CharField(max_length=120)
    recipient_phone = models.CharField(max_length=15)
    delivery_address = models.CharField(max_length=300)
    delivery_area_slug = models.CharField(max_length=80)
    delivery_area_name = models.CharField(max_length=120)
    delivery_date = models.DateField()
    delivery_slot_label = models.CharField(max_length=60)
    card_message = models.CharField(max_length=300, blank=True)
    mpesa_phone = models.CharField(max_length=15)

    subtotal_kes = models.PositiveIntegerField()
    delivery_fee_kes = models.PositiveIntegerField()
    total_kes = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="KES")

    idempotency_key = models.CharField(max_length=80)
    request_fingerprint = models.CharField(max_length=64, editable=False)
    expires_at = models.DateTimeField(help_text="Unpaid orders are cancelled after this time.")
    stock_released = models.BooleanField(default=False, editable=False)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["customer", "idempotency_key"], name="uniq_order_customer_idem_key"),
        ]

    def __str__(self):
        return self.reference


class OrderItem(models.Model):
    """Immutable purchase snapshot of one line."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalogue.Product", null=True, on_delete=models.SET_NULL, related_name="+")
    variant = models.ForeignKey("catalogue.ProductVariant", null=True, on_delete=models.SET_NULL, related_name="+")
    product_slug = models.CharField(max_length=80)
    product_name = models.CharField(max_length=160)
    variant_label = models.CharField(max_length=80)
    presentation_slug = models.CharField(max_length=60, blank=True)
    presentation_name = models.CharField(max_length=80, blank=True)
    quantity = models.PositiveIntegerField()
    unit_price_kes = models.PositiveIntegerField()
    line_total_kes = models.PositiveIntegerField()
    stock_reserved = models.PositiveIntegerField(default=0, editable=False)

    def __str__(self):
        return f"{self.quantity} x {self.product_name} ({self.variant_label})"


class OrderStatusEvent(models.Model):
    """Append-only history of order state changes."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
