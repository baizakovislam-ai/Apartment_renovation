from django.conf import settings
from django.db import models
from django.db.models import Q


class Remark(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Открыто"
        FIXED = "fixed", "Устранено, ожидает проверки"
        CLOSED = "closed", "Закрыто заказчиком"

    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="remarks")
    stage = models.ForeignKey("repairs.Stage", on_delete=models.PROTECT, related_name="remarks")
    description = models.TextField()
    responsible = models.ForeignKey("projects.ProjectMember", on_delete=models.PROTECT, related_name="assigned_remarks")
    due_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    version = models.PositiveIntegerField(default=1)
    request_id = models.UUIDField(unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_remarks")
    created_at = models.DateTimeField(auto_now_add=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="closed_remarks")
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "id"]
        constraints = [models.CheckConstraint(condition=Q(status__in=["open", "fixed", "closed"]), name="remark_status_valid")]


class RemarkReply(models.Model):
    remark = models.ForeignKey(Remark, on_delete=models.PROTECT, related_name="replies")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class StageDecision(models.Model):
    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="stage_decisions")
    stage = models.ForeignKey("repairs.Stage", on_delete=models.PROTECT, related_name="decisions")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=16, choices=[("submit", "Передан на приёмку"), ("accept", "Принят"), ("return", "Возвращён на доработку")])
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
