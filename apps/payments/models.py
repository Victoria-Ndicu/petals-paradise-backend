import uuid

from django.db import models


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    TERMINAL = {Status.PAID, Status.FAILED, Status.CANCELLED, Status.REFUNDED}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField(max_length=20, default="mpesa")
    amount_kes = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="KES")
    phone = models.CharField(max_length=15)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    failure_reason = models.CharField(max_length=80, blank=True)

    idempotency_key = models.CharField(max_length=80)
    merchant_request_id = models.CharField(max_length=80, blank=True)
    checkout_request_id = models.CharField(max_length=80, null=True, blank=True, unique=True)
    mpesa_receipt = models.CharField(max_length=40, blank=True)

    # Minimal callback metadata (credentials/PII-heavy raw payloads are NOT stored).
    callback_received_at = models.DateTimeField(null=True, blank=True)
    callback_result_code = models.IntegerField(null=True, blank=True)
    callback_result_desc = models.CharField(max_length=255, blank=True)
    callback_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_status_check_at = models.DateTimeField(null=True, blank=True)

    needs_review = models.BooleanField(default=False, help_text="Staff must look at this payment.")
    review_reason = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["order", "idempotency_key"], name="uniq_payment_order_idem_key"),
        ]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return f"{self.order.reference} {self.status} KES {self.amount_kes}"
