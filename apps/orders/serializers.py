from rest_framework import serializers

from apps.core.phone import normalize_ke_phone

from .models import Order, OrderItem, OrderStatusEvent


def _phone(value: str) -> str:
    phone = normalize_ke_phone(value)
    if not phone:
        raise serializers.ValidationError("Enter a valid Kenyan phone number.")
    return phone


class OrderLineInputSerializer(serializers.Serializer):
    product_id = serializers.CharField(max_length=80)
    variant_id = serializers.IntegerField(min_value=1)
    presentation_id = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    quantity = serializers.IntegerField(min_value=1, max_value=50)

    def to_internal_value(self, data):
        # Accept camelCase from the SPA; any price-like keys are simply ignored.
        mapped = {
            "product_id": data.get("productId", data.get("product_id")),
            "variant_id": data.get("variantId", data.get("variant_id")),
            "presentation_id": data.get("presentationId", data.get("presentation_id", "")),
            "quantity": data.get("quantity"),
        }
        try:
            return super().to_internal_value(mapped)
        except serializers.ValidationError as exc:
            # Re-key errors to camelCase for the client.
            rename = {"product_id": "productId", "variant_id": "variantId", "presentation_id": "presentationId"}
            raise serializers.ValidationError({rename.get(k, k): v for k, v in exc.detail.items()})


class OrderCreateSerializer(serializers.Serializer):
    items = OrderLineInputSerializer(many=True, allow_empty=False, max_length=20)
    recipient_name = serializers.CharField(max_length=120)
    recipient_phone = serializers.CharField(max_length=20)
    delivery_address = serializers.CharField(max_length=300)
    delivery_area = serializers.CharField(max_length=80)
    delivery_slot = serializers.IntegerField(min_value=1)
    delivery_date = serializers.DateField()
    card_message = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    mpesa_phone = serializers.CharField(max_length=20)

    CAMEL = {
        "recipientName": "recipient_name",
        "recipientPhone": "recipient_phone",
        "deliveryAddress": "delivery_address",
        "deliveryArea": "delivery_area",
        "deliverySlot": "delivery_slot",
        "deliveryDate": "delivery_date",
        "cardMessage": "card_message",
        "mpesaPhone": "mpesa_phone",
    }

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({"non_field_errors": ["Invalid payload."]})
        mapped = {self.CAMEL.get(k, k): v for k, v in data.items() if k in self.CAMEL or k in self.CAMEL.values() or k == "items"}
        try:
            return super().to_internal_value(mapped)
        except serializers.ValidationError as exc:
            reverse = {v: k for k, v in self.CAMEL.items()}
            detail = exc.detail
            if isinstance(detail, dict):
                detail = {reverse.get(k, k): v for k, v in detail.items()}
            raise serializers.ValidationError(detail)

    def validate_recipient_name(self, value):
        value = value.strip()
        if len(value) < 2:
            raise serializers.ValidationError("Enter the recipient's name.")
        return value

    def validate_recipient_phone(self, value):
        return _phone(value)

    def validate_mpesa_phone(self, value):
        return _phone(value)

    def validate_delivery_address(self, value):
        value = value.strip()
        if len(value) < 5:
            raise serializers.ValidationError("Enter a delivery address.")
        return value

    def validate_card_message(self, value):
        return value.strip()


class OrderItemSerializer(serializers.ModelSerializer):
    productId = serializers.CharField(source="product_slug")
    productName = serializers.CharField(source="product_name")
    variantLabel = serializers.CharField(source="variant_label")
    presentation = serializers.CharField(source="presentation_name")
    unitPriceKes = serializers.IntegerField(source="unit_price_kes")
    lineTotalKes = serializers.IntegerField(source="line_total_kes")

    class Meta:
        model = OrderItem
        fields = ["productId", "productName", "variantLabel", "presentation", "quantity", "unitPriceKes", "lineTotalKes"]


class OrderEventSerializer(serializers.ModelSerializer):
    from_ = serializers.CharField(source="from_status")
    to = serializers.CharField(source="to_status")
    at = serializers.DateTimeField(source="created_at")

    class Meta:
        model = OrderStatusEvent
        fields = ["from_", "to", "at"]


class OrderSerializer(serializers.ModelSerializer):
    recipient = serializers.SerializerMethodField()
    delivery = serializers.SerializerMethodField()
    items = OrderItemSerializer(many=True, read_only=True)
    subtotalKes = serializers.IntegerField(source="subtotal_kes")
    deliveryFeeKes = serializers.IntegerField(source="delivery_fee_kes")
    totalKes = serializers.IntegerField(source="total_kes")
    cardMessage = serializers.CharField(source="card_message")
    createdAt = serializers.DateTimeField(source="created_at")
    expiresAt = serializers.DateTimeField(source="expires_at")
    paidAt = serializers.DateTimeField(source="paid_at")
    payment = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "reference",
            "status",
            "recipient",
            "delivery",
            "cardMessage",
            "items",
            "subtotalKes",
            "deliveryFeeKes",
            "totalKes",
            "currency",
            "createdAt",
            "expiresAt",
            "paidAt",
            "payment",
        ]

    def get_recipient(self, obj):
        return {"name": obj.recipient_name, "phone": obj.recipient_phone}

    def get_delivery(self, obj):
        return {
            "address": obj.delivery_address,
            "area": obj.delivery_area_name,
            "areaId": obj.delivery_area_slug,
            "date": obj.delivery_date.isoformat(),
            "slot": obj.delivery_slot_label,
        }

    def get_payment(self, obj):
        payments = list(obj.payments.all())  # ordered newest first
        if not payments:
            return None
        paid = next((p for p in payments if p.status == "paid"), None)
        p = paid or payments[0]
        return {
            "id": str(p.id),
            "status": p.status,
            "failureReason": p.failure_reason,
            "receipt": p.mpesa_receipt if p.status == "paid" else "",
            "amountKes": p.amount_kes,
            "updatedAt": p.updated_at,
        }


class StaffOrderSerializer(OrderSerializer):
    customer = serializers.SerializerMethodField()
    events = OrderEventSerializer(many=True, read_only=True)
    mpesaPhone = serializers.CharField(source="mpesa_phone")

    class Meta(OrderSerializer.Meta):
        fields = OrderSerializer.Meta.fields + ["customer", "events", "mpesaPhone"]

    def get_customer(self, obj):
        return {"id": obj.customer_id, "name": obj.customer.name, "phone": obj.customer.phone}


class StatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Order.Status.choices)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
