"""Estimate versions and preliminary calculations (ТЗ FR09, FR10, FR16)."""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

CENTS = Decimal("0.01")


def money(value):
    return (value or Decimal("0")).quantize(CENTS, rounding=ROUND_HALF_UP)


class CostType(models.TextChoices):
    WORK = "work", "Работы"
    MATERIAL = "material", "Материалы"
    EQUIPMENT = "equipment", "Оборудование"
    DELIVERY = "delivery", "Доставка и вывоз"
    OTHER = "other", "Прочее"


UNIT_SUGGESTIONS = ["м²", "м", "м³", "шт", "кг", "т", "л", "компл.", "точка", "час", "день", "услуга"]


class Estimate(models.Model):
    """One version of the project estimate. Approved data is never edited — a new version is made instead (ТЗ 4.12)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        PENDING = "pending", "На согласовании"
        APPROVED = "approved", "Утверждена"
        REJECTED = "rejected", "Отклонена"
        SUPERSEDED = "superseded", "Заменена новой версией"

    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="estimates")
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    based_on = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="next_versions")
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    revision = models.PositiveIntegerField(default=1)
    comment = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_estimates")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="submitted_estimates")
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="decided_estimates")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="unique_estimate_version"),
            # Only one version can be worked on at a time: a draft or one waiting for a decision.
            models.UniqueConstraint(fields=["project"], condition=Q(status__in=["draft", "pending"]), name="one_open_estimate_per_project"),
            models.CheckConstraint(condition=Q(status__in=["draft", "pending", "approved", "rejected", "superseded"]), name="estimate_status_valid"),
            models.CheckConstraint(condition=Q(total__gte=0), name="estimate_total_not_negative"),
        ]

    def __str__(self):
        return f"Смета v{self.number}"

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT


class EstimateLine(models.Model):
    estimate = models.ForeignKey(Estimate, on_delete=models.CASCADE, related_name="lines")
    stage = models.ForeignKey("repairs.Stage", on_delete=models.PROTECT, related_name="estimate_lines")
    name = models.CharField(max_length=255)
    cost_type = models.CharField(max_length=16, choices=CostType.choices, default=CostType.WORK)
    unit = models.CharField(max_length=20)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["stage__position", "stage_id", "position", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="estimate_line_quantity_positive"),
            models.CheckConstraint(condition=Q(price__gte=0), name="estimate_line_price_not_negative"),
        ]

    @property
    def amount(self):
        return money(self.quantity * self.price)


class Calculation(models.Model):
    """Preliminary repair calculation: a quick «what will it cost» before the estimate (FR16)."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="calculations")
    name = models.CharField(max_length=200)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="calculations")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return self.name

    @property
    def total(self):
        return money(sum((item.amount for item in self.items.all()), Decimal("0")))


class CalculationItem(models.Model):
    calculation = models.ForeignKey(Calculation, on_delete=models.CASCADE, related_name="items")
    work = models.CharField(max_length=255)
    cost_type = models.CharField(max_length=16, choices=CostType.choices, default=CostType.WORK)
    unit = models.CharField(max_length=20)
    volume = models.DecimalField(max_digits=12, decimal_places=3)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(volume__gt=0), name="calculation_item_volume_positive"),
            models.CheckConstraint(condition=Q(price__gte=0), name="calculation_item_price_not_negative"),
        ]

    @property
    def amount(self):
        return money(self.volume * self.price)
