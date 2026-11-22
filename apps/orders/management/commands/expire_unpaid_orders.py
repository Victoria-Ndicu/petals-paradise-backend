from django.core.management.base import BaseCommand

from apps.payments.services import expire_stale_orders


class Command(BaseCommand):
    help = "Cancel unpaid orders past their payment deadline and release reserved stock (run every few minutes)."

    def handle(self, *args, **options):
        count = expire_stale_orders()
        self.stdout.write(f"Cancelled {count} expired unpaid order(s).")
