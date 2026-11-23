from django.urls import path

from . import views

urlpatterns = [
    path("auth/csrf", views.CsrfView.as_view()),
    path("auth/signup", views.SignupView.as_view()),
    path("auth/login", views.LoginView.as_view()),
    path("auth/logout", views.LogoutView.as_view()),
    path("auth/verify-email", views.VerifyEmailView.as_view()),
    path("auth/verify-email/resend", views.ResendEmailVerificationView.as_view()),
    path("auth/social/<str:provider>", views.SocialLoginView.as_view()),
    path("auth/social/<str:provider>/link", views.SocialLinkView.as_view()),
    path("me", views.MeView.as_view()),
]
