from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.core.models import record_audit

from .models import SocialAccount, User


class SocialAccountInline(admin.TabularInline):
    model = SocialAccount
    extra = 0
    can_delete = False
    readonly_fields = ("provider", "subject", "email", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    inlines = [SocialAccountInline]
    ordering = ("-date_joined",)
    list_display = ("name", "phone", "email", "is_staff", "is_active", "date_joined")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("name", "phone", "email")
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Profile", {"fields": ("name", "email", "default_delivery_area", "default_address")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("phone", "name", "email", "password1", "password2", "is_staff")}),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and {"is_staff", "is_superuser", "is_active"} & set(form.changed_data):
            record_audit(
                request.user, "user.permissions_changed", obj, changed=sorted(form.changed_data)
            )
