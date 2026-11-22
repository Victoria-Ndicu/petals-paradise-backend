from django.urls import path

from . import views

urlpatterns = [
    path("inquiries", views.InquiryCreateView.as_view()),
    path("admin/inquiries", views.StaffInquiryListView.as_view()),
    path("admin/inquiries/<uuid:pk>", views.StaffInquiryDetailView.as_view()),
]
