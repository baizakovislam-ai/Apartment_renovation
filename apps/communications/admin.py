from django.contrib import admin

from .models import Notification, NotificationSettings


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "project", "kind", "created_at", "read_at", "emailed_at")
    list_filter = ("kind", "project")
    search_fields = ("title", "text", "user__email")


@admin.register(NotificationSettings)
class NotificationSettingsAdmin(admin.ModelAdmin):
    list_display = ("user", "email_enabled")
