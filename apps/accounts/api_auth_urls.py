from django.urls import path
from .views import LoginView, LogoutView

app_name = "rest_framework"
urlpatterns = [
    path("login/", LoginView.as_view(redirect_authenticated_user=False), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
]
