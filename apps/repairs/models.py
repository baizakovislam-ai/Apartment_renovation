from django.conf import settings
from django.db import models
from django.db.models import F, Q


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
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    responsible = models.ForeignKey("projects.ProjectMember", on_delete=models.PROTECT, related_name="responsible_stages", null=True, blank=True)
    position = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PLANNED)
    acceptance_version = models.PositiveIntegerField(default=1)
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
    version = models.PositiveIntegerField(default=1)
    request_id = models.UUIDField(unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_tasks")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "id"]
        constraints = [models.CheckConstraint(condition=Q(status__in=["todo", "in_progress", "review", "done"]), name="task_status_valid")]

    def __str__(self):
        return self.name
