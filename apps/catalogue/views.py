from datetime import timedelta

from django.conf import settings
from django.db.models import Min, Prefetch, Q
from django.utils import timezone
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from zoneinfo import ZoneInfo

from .models import BlockedDate, DeliveryOption, DeliverySlot, Product, ProductPresentation, ProductVariant
from .serializers import DeliveryOptionSerializer, DeliverySlotSerializer, ProductSerializer


def published_products():
    return (
        Product.objects.filter(status=Product.Status.PUBLISHED, category__is_active=True)
        .select_related("category")
        .prefetch_related(
            Prefetch("variants", queryset=ProductVariant.objects.filter(is_active=True).select_related("product")),
            Prefetch(
                "product_presentations",
                queryset=ProductPresentation.objects.select_related("presentation"),
            ),
        )
        .annotate(startingPriceKes=Min("variants__price_kes", filter=Q(variants__is_active=True)))
    )


class ProductListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = ProductSerializer
    filter_backends = []  # explicit, small filter set below

    ORDERING = {
        "name": "name",
        "price": "startingPriceKes",
        "-price": "-startingPriceKes",
    }

    def get_queryset(self):
        qs = published_products()
        params = self.request.query_params
        category = params.get("category")
        if category:
            qs = qs.filter(category__slug=category)
        search = (params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(description__icontains=search) | Q(category__name__icontains=search)
            )
        order = self.ORDERING.get(params.get("ordering", "name"), "name")
        # secondary key keeps pagination stable
        return qs.order_by(order, "id")


class ProductDetailView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = ProductSerializer
    lookup_field = "slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        return published_products()


class DeliveryOptionsView(APIView):
    """Areas, fees, time slots, blocked dates and lead-time rules."""

    permission_classes = [AllowAny]

    def get(self, request):
        tz = ZoneInfo(settings.BUSINESS_TIME_ZONE)
        today = timezone.now().astimezone(tz).date()
        horizon = today + timedelta(days=settings.MAX_DELIVERY_DAYS_AHEAD)
        blocked = BlockedDate.objects.filter(date__gte=today, date__lte=horizon).values_list("date", flat=True)
        return Response(
            {
                "currency": settings.CURRENCY,
                "timeZone": settings.BUSINESS_TIME_ZONE,
                "minLeadHours": settings.MIN_DELIVERY_LEAD_HOURS,
                "maxDaysAhead": settings.MAX_DELIVERY_DAYS_AHEAD,
                "areas": DeliveryOptionSerializer(DeliveryOption.objects.filter(is_active=True), many=True).data,
                "slots": DeliverySlotSerializer(DeliverySlot.objects.filter(is_active=True), many=True).data,
                "blockedDates": [d.isoformat() for d in blocked],
            }
        )
