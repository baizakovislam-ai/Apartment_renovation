from django.urls import path

from . import views

urlpatterns = [
    path("notifications/", views.NotificationListView.as_view(), name="notification-list"),
    path("notifications/read-all/", views.NotificationReadAllView.as_view(), name="notification-read-all"),
    path("notifications/settings/", views.NotificationSettingsView.as_view(), name="notification-settings"),
    path("notifications/<int:pk>/read/", views.NotificationReadView.as_view(), name="notification-read"),
]
