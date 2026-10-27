import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.repairs.models import photo_upload_path, private_storage


class Remark(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Открыто"
        FIXED = "fixed", "Устранено, ждёт проверки"
        CLOSED = "closed", "Закрыто"

    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="remarks")
    stage = models.ForeignKey("repairs.Stage", on_delete=models.PROTECT, related_name="remarks")
    task = models.ForeignKey("repairs.Task", on_delete=models.PROTECT, null=True, blank=True, related_name="remarks")
    description = models.TextField()
    responsible = models.ForeignKey("projects.ProjectMember", on_delete=models.PROTECT, related_name="assigned_remarks")
    due_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    version = models.PositiveIntegerField(default=1)
    request_id = models.UUIDField(default=uuid.uuid4, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_remarks")
    created_at = models.DateTimeField(auto_now_add=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="closed_remarks")
    closed_at = models.DateTimeField(null=True, blank=True)
    withdrawn = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "id"]
        constraints = [models.CheckConstraint(condition=Q(status__in=["open", "fixed", "closed"]), name="remark_status_valid")]

    def __str__(self):
        return self.description[:60]

    @property
    def is_overdue(self):
        return self.status == self.Status.OPEN and self.due_date < timezone.localdate()


class RemarkComment(models.Model):
    """Replies and status notes in a remark thread."""

    remark = models.ForeignKey(Remark, on_delete=models.PROTECT, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="remark_comments")
    text = models.TextField()
    action = models.CharField(max_length=16, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class StageDecision(models.Model):
    """Who handed a stage over or decided on it, and when (ТЗ 4.12)."""

    class Action(models.TextChoices):
        SUBMIT = "submit", "Передан на приёмку"
        ACCEPT = "accept", "Принят"
        RETURN = "return", "Возвращён на доработку"

    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="stage_decisions")
    stage = models.ForeignKey("repairs.Stage", on_delete=models.PROTECT, related_name="decisions")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stage_decisions")
    action = models.CharField(max_length=16, choices=Action.choices)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class RemarkPhoto(models.Model):
    """Photo of a defect (on creation) or of its fix/reply; served only through an access-checked view."""

    remark = models.ForeignKey(Remark, on_delete=models.CASCADE, related_name="photos")
    comment = models.ForeignKey(RemarkComment, on_delete=models.CASCADE, null=True, blank=True, related_name="photos")
    file = models.FileField(upload_to=photo_upload_path, storage=private_storage, max_length=255)
    content_type = models.CharField(max_length=32)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="remark_photos")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
