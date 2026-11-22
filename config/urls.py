from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path


def healthz(request):
    """Render health check: confirms the app and database are reachable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:  # noqa: BLE001
        return JsonResponse({"status": "unhealthy"}, status=503)
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", healthz),
    path("admin/", admin.site.urls),
    path(
        "api/v1/",
        include(
            [
                path("", include("apps.accounts.urls")),
                path("", include("apps.catalogue.urls")),
                path("", include("apps.orders.urls")),
                path("", include("apps.payments.urls")),
                path("", include("apps.inquiries.urls")),
            ]
        ),
    ),
]
