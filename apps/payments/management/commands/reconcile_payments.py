from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.payments.models import Payment
from apps.payments.services import LOCAL_CLOSE_REASONS, reconcile_payment


class Command(BaseCommand):
    help = (
        "Ask Safaricom for the outcome of payments that are still pending after a minute, and re-check "
        "recently timed-out/cancelled ones in case money arrived late (run every few minutes)."
    )

    def handle(self, *args, **options):
        now = timezone.now()
        pending = Payment.objects.filter(
            Q(status=Payment.Status.PENDING, created_at__lte=now - timedelta(minutes=1))
            | Q(
                status__in=[Payment.Status.FAILED, Payment.Status.CANCELLED],
                failure_reason__in=LOCAL_CLOSE_REASONS,
                created_at__gte=now - timedelta(hours=6),
            ),
            checkout_request_id__isnull=False,
        )
        total = pending.count()
        changed = sum(1 for p in pending if reconcile_payment(p.pk, force=True))
        flagged = Payment.objects.filter(needs_review=True).count()
        self.stdout.write(f"Checked {total} payment(s); resolved {changed}.")
        if flagged:
            self.stdout.write(self.style.WARNING(f"{flagged} payment(s) need staff review (see admin > Payments)."))
