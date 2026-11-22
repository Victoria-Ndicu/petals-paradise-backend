import re

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.exceptions import ApiError

from . import services
from .models import Order
from .serializers import (
    OrderCreateSerializer,
    OrderSerializer,
    StaffOrderSerializer,
    StatusChangeSerializer,
)

_IDEM_RE = re.compile(r"^[A-Za-z0-9_\-:.]{8,80}$")


def get_idempotency_key(request) -> str:
    key = request.headers.get("Idempotency-Key", "").strip()
    if not _IDEM_RE.match(key):
        raise ApiError(
            "IDEMPOTENCY_KEY_REQUIRED",
            "An Idempotency-Key header (8-80 letters, digits, - _ : .) is required.",
            status.HTTP_400_BAD_REQUEST,
        )
    return key


def order_queryset():
    return Order.objects.select_related("customer").prefetch_related("items", "events")


class OrderCreateView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "order_create"

    def post(self, request):
        key = get_idempotency_key(request)
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order, created = services.create_order(
            customer=request.user, data=serializer.validated_data, idempotency_key=key
        )
        order = order_queryset().get(pk=order.pk)
        return Response(
            OrderSerializer(order).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        qs = order_queryset()
        if not request.user.is_staff:
            qs = qs.filter(customer=request.user)  # others get 404, not 403: no existence leak
        order = get_object_or_404(qs, pk=order_id)

        serializer = StaffOrderSerializer if request.user.is_staff else OrderSerializer
        return Response(serializer(order).data)


class MyOrdersView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrderSerializer

    def get_queryset(self):
        return order_queryset().filter(customer=self.request.user)


class OrderCancelView(APIView):
    """Customers may cancel only while payment has not completed."""

    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        order = get_object_or_404(Order.objects.filter(customer=request.user), pk=order_id)
        if not services.cancel_unpaid_order(order.pk, actor=request.user, note="cancelled by customer"):
            raise ApiError(
                "CANNOT_CANCEL",
                "This order can no longer be cancelled online. Please contact us.",
                status.HTTP_409_CONFLICT,
            )
        return Response(OrderSerializer(order_queryset().get(pk=order.pk)).data)


# ------------------------------------------------------------------ staff API
class StaffOrderListView(generics.ListAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = StaffOrderSerializer

    def get_queryset(self):
        qs = order_queryset()
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("date"):
            qs = qs.filter(delivery_date=params["date"])
        q = (params.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(reference__icontains=q) | Q(customer__phone__icontains=q))
        return qs


class StaffOrderDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = StaffOrderSerializer
    lookup_url_kwarg = "order_id"

    def get_queryset(self):
        return order_queryset()


class StaffOrderStatusView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, order_id):
        serializer = StatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]

        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=get_object_or_404(Order, pk=order_id).pk)
            # Pending orders can only be confirmed (payment received) or cancelled: the state
            # machine rejects anything else, so an unpaid order can never be fulfilled.
            if new_status == Order.Status.CONFIRMED and order.status == Order.Status.PENDING_PAYMENT:
                note = serializer.validated_data["note"] or "Payment confirmed manually by staff"
                services.confirm_order_payment(order.pk, actor=request.user, note=note)
                order.refresh_from_db()
            else:
                services.transition_order(
                    order, new_status, actor=request.user, note=serializer.validated_data["note"]
                )
        return Response(StaffOrderSerializer(order_queryset().get(pk=order.pk)).data)
