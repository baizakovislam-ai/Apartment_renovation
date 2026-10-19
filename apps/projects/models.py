import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


def invitation_token():
    return secrets.token_urlsafe(32)


def invitation_expiry():
    return timezone.now() + timedelta(days=7)


class Project(models.Model):
    """Minimal project foundation; project lifecycle belongs to the projects owner."""

    name = models.CharField(max_length=200)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_projects")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name


class ProjectMember(models.Model):
    class Role(models.TextChoices):
        CUSTOMER = "customer", "Заказчик"
        FOREMAN = "foreman", "Прораб"
        EXECUTOR = "executor", "Исполнитель"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="project_memberships")
    role = models.CharField(max_length=16, choices=Role.choices)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="unique_project_member"),
            models.UniqueConstraint(fields=["project"], condition=Q(role="customer"), name="one_customer_per_project"),
            models.UniqueConstraint(fields=["project"], condition=Q(role="foreman"), name="one_foreman_per_project"),
            models.CheckConstraint(condition=Q(role__in=["customer", "foreman", "executor"]), name="valid_member_role"),
        ]


class ProjectInvitation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        ACCEPTED = "accepted", "Принято"
        CANCELLED = "cancelled", "Отменено"
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
