from django.contrib import admin

from apps.core.models import record_audit

from .models import Inquiry


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "name", "phone", "occasion", "preferred_contact", "status", "assigned_to")
    list_filter = ("status", "occasion", "preferred_contact")
    search_fields = ("name", "phone", "details")
    list_editable = ("status", "assigned_to")
    readonly_fields = ("name", "phone", "occasion", "preferred_contact", "details", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_audit(request.user, "inquiry.updated", obj, changed=sorted(form.changed_data))
