import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


def invitation_token():
    return secrets.token_urlsafe(32)


def invitation_expiry():
    return timezone.now() + timedelta(days=7)


class Project(models.Model):
    class Status(models.TextChoices):
        PLANNING = "planning", "Планирование"
        IN_PROGRESS = "in_progress", "В работе"
        COMPLETED = "completed", "Завершён"

    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255, blank=True)
    area = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    budget = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNING)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_projects")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Archive instead of deleting a project that already has work records (ТЗ 4.12: отмена с указанием причины).
    archived_at = models.DateTimeField(null=True, blank=True)
    archive_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(start_date__isnull=True) | Q(end_date__gte=F("start_date")), name="project_dates_valid"),
            models.CheckConstraint(condition=Q(area__isnull=True) | Q(area__gt=0), name="project_area_positive"),
            models.CheckConstraint(condition=Q(budget__isnull=True) | Q(budget__gte=0), name="project_budget_not_negative"),
            models.CheckConstraint(condition=Q(status__in=["planning", "in_progress", "completed"]), name="project_status_valid"),
        ]

    def __str__(self):
        return self.name

    @property
    def is_archived(self):
        return self.archived_at is not None


class ProjectMember(models.Model):
    class Role(models.TextChoices):
        CUSTOMER = "customer", "Заказчик"
        FOREMAN = "foreman", "Прораб"
        EXECUTOR = "executor", "Исполнитель"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="project_memberships")
    role = models.CharField(max_length=16, choices=Role.choices)
    joined_at = models.DateTimeField(auto_now_add=True)
    # A removed member keeps the row so finished tasks, stages and history stay attributed to them.
    is_active = models.BooleanField(default=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="removed_memberships")

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="unique_project_member"),
            models.UniqueConstraint(fields=["project"], condition=Q(role="customer", is_active=True), name="one_active_customer_per_project"),
            models.UniqueConstraint(fields=["project"], condition=Q(role="foreman", is_active=True), name="one_active_foreman_per_project"),
            models.CheckConstraint(condition=Q(role__in=["customer", "foreman", "executor"]), name="valid_member_role"),
        ]


class ProjectInvitation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        ACCEPTED = "accepted", "Принято"
        CANCELLED = "cancelled", "Отменено"
        DECLINED = "declined", "Отклонено"
        EXPIRED = "expired", "Истекло"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=16, choices=[(r, label) for r, label in ProjectMember.Role.choices if r != "customer"])
    token = models.CharField(max_length=64, unique=True, default=invitation_token, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sent_project_invitations")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=invitation_expiry)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "email"], condition=Q(status="pending"), name="unique_pending_invitation"),
            models.UniqueConstraint(fields=["project"], condition=Q(status="pending", role="foreman"), name="one_pending_foreman_invite"),
            models.CheckConstraint(condition=Q(role__in=["foreman", "executor"]), name="valid_invitation_role"),
        ]


class ProjectEvent(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="project_events")
    action = models.CharField(max_length=80)
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
