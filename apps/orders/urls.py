from django.urls import path

from . import views

urlpatterns = [
    path("orders", views.OrderCreateView.as_view()),
    path("orders/<uuid:order_id>", views.OrderDetailView.as_view()),
    path("orders/<uuid:order_id>/cancel", views.OrderCancelView.as_view()),
    path("me/orders", views.MyOrdersView.as_view()),
    path("admin/orders", views.StaffOrderListView.as_view()),
    path("admin/orders/<uuid:order_id>", views.StaffOrderDetailView.as_view()),
    path("admin/orders/<uuid:order_id>/status", views.StaffOrderStatusView.as_view()),
]
