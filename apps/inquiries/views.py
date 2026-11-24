import logging

from django.db import transaction
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.models import record_audit

from .models import Inquiry
from .notifications import notify_inquiry_received
from .serializers import InquiryCreateSerializer, InquiryReadSerializer

logger = logging.getLogger(__name__)


class InquiryCreateView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []  # public form; no cookies involved, so no CSRF surface
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "inquiry"

    def post(self, request):
        serializer = InquiryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data.get("website"):
            # Honeypot tripped. Answer like a success so bots learn nothing, but store nothing.
            logger.info("Inquiry honeypot triggered")
            return Response({"id": None, "status": "new"}, status=status.HTTP_201_CREATED)

        inquiry = Inquiry.objects.create(
            name=data["name"],
            phone=data["phone"],
            email=data["email"],
            occasion=data["occasion"],
            preferred_contact=data["preferred_contact"],
            details=data["details"],
        )
        transaction.on_commit(lambda: notify_inquiry_received(inquiry))

        return Response(
            {"id": str(inquiry.pk), "status": inquiry.status, "createdAt": inquiry.created_at},
            status=status.HTTP_201_CREATED,
        )


class StaffInquiryListView(generics.ListAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = InquiryReadSerializer

    def get_queryset(self):
        qs = Inquiry.objects.select_related("assigned_to")
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs


class StaffInquiryDetailView(generics.RetrieveUpdateAPIView):
    """Staff can change status and assignment only; customer-provided fields are read-only."""

    permission_classes = [IsAdminUser]
    serializer_class = InquiryReadSerializer
    queryset = Inquiry.objects.select_related("assigned_to")
    http_method_names = ["get", "patch", "head", "options"]

    def perform_update(self, serializer):
        instance = serializer.save()
        record_audit(
            self.request.user, "inquiry.updated", instance, changed=sorted(serializer.validated_data.keys())
        )
