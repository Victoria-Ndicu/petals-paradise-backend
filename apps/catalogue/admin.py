from django.contrib import admin
from django.db import transaction

from apps.core.models import record_audit

from .brevo import sync_product
from .models import (
    BlockedDate,
    Category,
    DeliveryOption,
    DeliverySlot,
    Presentation,
    Product,
    ProductPresentation,
    ProductVariant,
)


class AuditedAdmin(admin.ModelAdmin):
    """Records who changed prices/availability/delivery config."""

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_audit(
            request.user,
            f"{obj.__class__.__name__.lower()}.{'updated' if change else 'created'}",
            obj,
            changed=sorted(form.changed_data),
        )

    def delete_model(self, request, obj):
        record_audit(request.user, f"{obj.__class__.__name__.lower()}.deleted", obj, repr=str(obj))
        super().delete_model(request, obj)


class VariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


class PresentationInline(admin.TabularInline):
    model = ProductPresentation
    extra = 1


@admin.register(Product)
class ProductAdmin(AuditedAdmin):
    list_display = ("name", "category", "status", "stock_policy", "is_seasonal", "updated_at")
    list_filter = ("status", "category", "stock_policy", "is_seasonal")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [VariantInline, PresentationInline]
    actions = ["publish", "unpublish", "retire"]

    @admin.action(description="Publish selected products")
    def publish(self, request, queryset):
        for p in queryset:
            p.status = Product.Status.PUBLISHED
            p.save(update_fields=["status", "updated_at"])
            record_audit(request.user, "product.published", p)
            transaction.on_commit(lambda p=p: sync_product(p))

    @admin.action(description="Unpublish selected products (back to draft)")
    def unpublish(self, request, queryset):
        for p in queryset:
            p.status = Product.Status.DRAFT
            p.save(update_fields=["status", "updated_at"])
            record_audit(request.user, "product.unpublished", p)
            transaction.on_commit(lambda p=p: sync_product(p))

    @admin.action(description="Retire selected products")
    def retire(self, request, queryset):
        for p in queryset:
            p.status = Product.Status.RETIRED
            p.save(update_fields=["status", "updated_at"])
            record_audit(request.user, "product.retired", p)
            transaction.on_commit(lambda p=p: sync_product(p))

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        if formset.has_changed():
            record_audit(request.user, "product.variants_changed", form.instance)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        transaction.on_commit(lambda: sync_product(form.instance))


@admin.register(Category)
class CategoryAdmin(AuditedAdmin):
    list_display = ("name", "slug", "sort_order", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Presentation)
class PresentationAdmin(AuditedAdmin):
    list_display = ("name", "slug", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(DeliveryOption)
class DeliveryOptionAdmin(AuditedAdmin):
    list_display = ("name", "fee_kes", "is_active", "sort_order")
    list_editable = ("fee_kes", "is_active", "sort_order")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(DeliverySlot)
class DeliverySlotAdmin(AuditedAdmin):
    list_display = ("label", "start_time", "end_time", "is_active")


@admin.register(BlockedDate)
class BlockedDateAdmin(AuditedAdmin):
    list_display = ("date", "reason")
