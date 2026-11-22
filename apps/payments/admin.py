from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Read-only: payment state is changed only by the verified M-Pesa flow."""

    list_display = ("created_at", "order", "amount_kes", "status", "mpesa_receipt", "needs_review")
    list_filter = ("status", "needs_review")
    search_fields = ("order__reference", "mpesa_receipt", "checkout_request_id")
    readonly_fields = [f.name for f in Payment._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
