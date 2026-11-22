from django.urls import path

from . import views

urlpatterns = [
    path("orders/<uuid:order_id>/payments/mpesa", views.MpesaPaymentView.as_view()),
    path("webhooks/mpesa", views.MpesaWebhookView.as_view()),
    path("webhooks/mpesa/<str:secret>", views.MpesaWebhookView.as_view()),
]
