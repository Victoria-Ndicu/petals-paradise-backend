from datetime import time, timedelta
from itertools import count

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.catalogue.models import (
    BlockedDate,
    Category,
    DeliveryOption,
    DeliverySlot,
    Presentation,
    Product,
    ProductPresentation,
    ProductVariant,
)
from apps.orders.services import business_now

User = get_user_model()
_keys = count(1)

MPESA_SETTINGS = dict(
    MPESA_ENV="sandbox",
    MPESA_CONSUMER_KEY="key",
    MPESA_CONSUMER_SECRET="secret",
    MPESA_SHORTCODE="174379",
    MPESA_PASSKEY="passkey",
    MPESA_CALLBACK_BASE_URL="https://api.example.com",
    MPESA_CALLBACK_SECRET="s3cret",
)


def idem() -> str:
    return f"test-key-{next(_keys):06d}"


@override_settings(**MPESA_SETTINGS)
class BaseTest(APITestCase):
    PASSWORD = "Str0ng-pass-123"

    def setUp(self):
        cache.clear()
        self.flowers = Category.objects.create(slug="flowers", name="Flowers")
        self.gifts = Category.objects.create(slug="fruit-gifts", name="Fruit & gifts")
        self.bouquet = Presentation.objects.create(slug="bouquet", name="Bouquet")
        self.box = Presentation.objects.create(slug="box", name="Gift box")

        self.roses = Product.objects.create(
            slug="roses", category=self.flowers, name="Roses", status=Product.Status.PUBLISHED
        )
        self.small = ProductVariant.objects.create(product=self.roses, label="Small", price_kes=2500, sort_order=0)
        self.large = ProductVariant.objects.create(product=self.roses, label="Large", price_kes=4000, sort_order=1)
        ProductPresentation.objects.create(product=self.roses, presentation=self.bouquet)
        ProductPresentation.objects.create(product=self.roses, presentation=self.box, price_adjustment_kes=500)

        self.draft = Product.objects.create(slug="draft-item", category=self.flowers, name="Draft", status="draft")
        ProductVariant.objects.create(product=self.draft, label="One", price_kes=1000)

        self.tracked = Product.objects.create(
            slug="tracked", category=self.gifts, name="Tracked", status="published", stock_policy="tracked"
        )
        self.tracked_v = ProductVariant.objects.create(
            product=self.tracked, label="Std", price_kes=1000, stock_quantity=3
        )

        self.area = DeliveryOption.objects.create(slug="westlands", name="Westlands", fee_kes=400)
        self.inactive_area = DeliveryOption.objects.create(slug="mars", name="Mars", fee_kes=1, is_active=False)
        self.slot = DeliverySlot.objects.create(label="9am - 12pm", start_time=time(9), end_time=time(12))

        self.customer = User.objects.create_user("0712345678", self.PASSWORD, name="Jane", email="jane@example.com")
        self.other = User.objects.create_user("0722000111", self.PASSWORD, name="Other")
        self.staff = User.objects.create_user("0733000222", self.PASSWORD, name="Staff", is_staff=True)

        self.client = APIClient()

    # helpers ---------------------------------------------------------
    def login(self, user):
        self.client.force_login(user, backend="apps.accounts.backends.PhoneOrEmailBackend")

    def delivery_date(self, days=3):
        return (business_now().date() + timedelta(days=days)).isoformat()

    def order_payload(self, **overrides):
        payload = {
            "items": [{"productId": "roses", "variantId": self.small.pk, "presentationId": "bouquet", "quantity": 2}],
            "recipientName": "Mary",
            "recipientPhone": "0700111222",
            "deliveryAddress": "Westlands, Mpaka Road 5",
            "deliveryArea": "westlands",
            "deliverySlot": self.slot.pk,
            "deliveryDate": self.delivery_date(),
            "cardMessage": "Happy birthday",
            "mpesaPhone": "0712345678",
        }
        payload.update(overrides)
        return payload

    def place_order(self, user=None, key=None, **overrides):
        self.login(user or self.customer)
        return self.client.post(
            "/api/v1/orders", self.order_payload(**overrides), format="json", headers={"Idempotency-Key": key or idem()}
        )
