import hmac
import logging

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.orders.models import Order
from apps.orders.views import get_idempotency_key

from . import services

logger = logging.getLogger(__name__)


class MpesaPaymentView(APIView):
    """Start an idempotent STK Push for the signed-in customer's own order."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payment_init"

    def post(self, request, order_id):
        key = get_idempotency_key(request)
        order = get_object_or_404(Order.objects.filter(customer=request.user), pk=order_id)
        payment = services.initiate_mpesa_payment(order.pk, key)
        # Only safe identifiers and a status leave the server; no provider payloads.
        return Response(
            {
                "orderId": str(order.pk),
                "paymentId": str(payment.pk),
                "status": payment.status,
                "message": "Check your phone and enter your M-Pesa PIN to complete payment.",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class MpesaWebhookView(APIView):
    """Daraja STK callback. Unauthenticated by design (Safaricom calls it), so it
    is protected by an unguessable URL secret, structural validation, a match
    against a payment we created, an amount check, and a status-query verification."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, secret: str = ""):
        expected = settings.MPESA_CALLBACK_SECRET
        if expected and not hmac.compare_digest(secret.encode(), expected.encode()):
            raise Http404()
        if not expected and not settings.DEBUG and settings.MPESA_ENV == "production":
            raise Http404()  # never accept unauthenticated callbacks in production

        try:
            outcome = services.handle_callback(request.data)
        except services.InvalidCallback:
            return Response({"ResultCode": 1, "ResultDesc": "Invalid payload"}, status=status.HTTP_400_BAD_REQUEST)
        logger.info("M-Pesa callback processed: %s", outcome)
        return Response({"ResultCode": 0, "ResultDesc": "Accepted"})
