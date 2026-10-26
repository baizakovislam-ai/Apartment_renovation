import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

DUE_SOON_DAYS = 2


class Stage(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Запланирован"
        IN_PROGRESS = "in_progress", "В работе"
        COMPLETED = "completed", "Работы выполнены"
        PENDING_ACCEPTANCE = "pending_acceptance", "На приёмке"
        ACCEPTED = "accepted", "Принят"
        CANCELLED = "cancelled", "Отменён"

    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="stages")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    responsible = models.ForeignKey("projects.ProjectMember", on_delete=models.PROTECT, related_name="responsible_stages")
    position = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PLANNED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_stages")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="stage_dates_valid"),
            models.CheckConstraint(condition=Q(status__in=["planned", "in_progress", "completed", "pending_acceptance", "accepted", "cancelled"]), name="stage_status_valid"),
        ]

    def __str__(self):
        return self.name


class Task(models.Model):
    class Status(models.TextChoices):
        TODO = "todo", "К выполнению"
        IN_PROGRESS = "in_progress", "В работе"
        REVIEW = "review", "На проверке"
        DONE = "done", "Выполнена"

    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="tasks")
    stage = models.ForeignKey(Stage, on_delete=models.PROTECT, related_name="tasks")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey("projects.ProjectMember", on_delete=models.PROTECT, related_name="assigned_tasks")
    due_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TODO)
    result = models.TextField(blank=True)
    return_reason = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    request_id = models.UUIDField(default=uuid.uuid4, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_tasks")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_tasks")

    class Meta:
        ordering = ["due_date", "id"]
        indexes = [models.Index(fields=["project", "status"]), models.Index(fields=["project", "due_date"])]
        constraints = [models.CheckConstraint(condition=Q(status__in=["todo", "in_progress", "review", "done"]), name="task_status_valid")]

    def __str__(self):
        return self.name

    @property
    def is_overdue(self):
        return self.status != self.Status.DONE and self.due_date < timezone.localdate()

    @property
    def is_due_soon(self):
        today = timezone.localdate()
        return self.status != self.Status.DONE and today <= self.due_date <= today + timedelta(days=DUE_SOON_DAYS)


def photo_upload_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower()
    return f"photos/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.{ext}"


class PrivateStorage(FileSystemStorage):
    """Storage outside any public URL; the location is read from settings on every access."""

    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)


def private_storage():
    return PrivateStorage()


class PhotoReport(models.Model):
    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="photo_reports")
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="reports")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="photo_reports")
    description = models.TextField()
    request_id = models.UUIDField(default=uuid.uuid4, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Photo(models.Model):
    report = models.ForeignKey(PhotoReport, on_delete=models.CASCADE, related_name="photos")
    file = models.FileField(upload_to=photo_upload_path, storage=private_storage, max_length=255)
    content_type = models.CharField(max_length=32)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size = models.PositiveIntegerField()

    class Meta:
        ordering = ["id"]
