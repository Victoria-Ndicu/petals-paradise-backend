from django.contrib import admin

from .models import Order, OrderItem, OrderStatusEvent


class ItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in OrderItem._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False


class EventInline(admin.TabularInline):
    model = OrderStatusEvent
    extra = 0
    can_delete = False
    readonly_fields = ["from_status", "to_status", "actor", "note", "created_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Read-only overview. Status changes go through the API so the state
    machine, audit trail and stock release always run."""

    list_display = ("reference", "status", "customer", "total_kes", "delivery_date", "delivery_slot_label", "created_at")
    list_filter = ("status", "delivery_date", "delivery_area_slug")
    search_fields = ("reference", "customer__phone", "recipient_name", "recipient_phone")
    inlines = [ItemInline, EventInline]
    readonly_fields = [f.name for f in Order._meta.fields]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
