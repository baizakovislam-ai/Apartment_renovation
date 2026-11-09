"""Notifications on the site and by email (ТЗ FR19)."""
from django.conf import settings
from django.db import models
from django.db.models import Q


class Notification(models.Model):
    class Kind(models.TextChoices):
        TASK = "task", "Задачи"
        REMARK = "remark", "Замечания"
        APPROVAL = "approval", "Согласования"
        DEADLINE = "deadline", "Сроки"
        OVERDUE = "overdue", "Просрочки"
        PROJECT = "project", "Проект"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="notifications")
    event = models.ForeignKey("projects.ProjectEvent", on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    title = models.CharField(max_length=255)
    text = models.TextField(blank=True)
    url = models.CharField(max_length=255, blank=True)
    # Deadline reminders are created by a daily check; the key keeps them from repeating.
    key = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    emailed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "read_at"])]
        constraints = [
            models.UniqueConstraint(fields=["user", "key"], condition=~Q(key=""), name="unique_notification_key"),
            models.CheckConstraint(condition=Q(kind__in=["task", "remark", "approval", "deadline", "overdue", "project"]), name="notification_kind_valid"),
        ]

    def __str__(self):
        return self.title

    @property
    def is_read(self):
        return self.read_at is not None


class NotificationSettings(models.Model):
    """Per-user choice: notifications always appear on the site; email can be turned off."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_settings")
    email_enabled = models.BooleanField("Присылать уведомления на Email", default=True)
