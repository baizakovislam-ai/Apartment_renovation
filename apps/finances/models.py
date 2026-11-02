"""Expenses of the project (ТЗ FR12) — the actual money spent, checked by the customer.

Statuses (ТЗ прил. Б): Черновик → На подтверждении → Подтверждён / Отклонён.
Only confirmed expenses count as actual costs; rejected ones stay in the list with the reason (ТЗ 4.12).
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.estimates.models import CostType
from apps.repairs.models import private_storage


def document_upload_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower()
    return f"expense-documents/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.{ext}"


class Expense(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        PENDING = "pending", "На подтверждении"
        CONFIRMED = "confirmed", "Подтверждён"
        REJECTED = "rejected", "Отклонён"

    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="expenses")
    number = models.PositiveIntegerField()
    stage = models.ForeignKey("repairs.Stage", on_delete=models.PROTECT, null=True, blank=True, related_name="expenses")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField()
    recipient = models.CharField(max_length=200)
    purpose = models.CharField(max_length=255)
    category = models.CharField(max_length=16, choices=CostType.choices, default=CostType.MATERIAL)
    note = models.TextField(blank=True)
    document = models.FileField(upload_to=document_upload_path, storage=private_storage, max_length=255, blank=True)
    document_name = models.CharField(max_length=255, blank=True)
    document_type = models.CharField(max_length=32, blank=True)
    document_size = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    revision = models.PositiveIntegerField(default=1)
    request_id = models.UUIDField(default=uuid.uuid4, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_expenses")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="submitted_expenses")
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="decided_expenses")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-number"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="unique_expense_number"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="expense_amount_positive"),
            models.CheckConstraint(condition=Q(status__in=["draft", "pending", "confirmed", "rejected"]), name="expense_status_valid"),
            # A decision always has its author and time (ТЗ 4.12).
            models.CheckConstraint(condition=Q(status__in=["draft", "pending"]) | Q(decided_by__isnull=False, decided_at__isnull=False),
                                   name="expense_decision_has_author"),
        ]

    def __str__(self):
        return f"Расход №{self.number}"

    @property
    def name(self):
        """Short line for lists and the final report."""
        return f"{self.purpose} — {self.recipient}"

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT
