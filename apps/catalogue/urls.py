from django.urls import path

from . import views

urlpatterns = [
    path("products", views.ProductListView.as_view()),
    path("products/<slug:slug>", views.ProductDetailView.as_view()),
    path("delivery-options", views.DeliveryOptionsView.as_view()),
]
