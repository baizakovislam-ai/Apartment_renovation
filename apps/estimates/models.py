"""Estimate versions, estimate change requests and preliminary calculations (ТЗ FR09, FR10, FR11, FR16)."""
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


class ChangeRequest(models.Model):
    """A request to change the approved estimate (ТЗ FR11).

    It names the reason, the changed lines (new values) and an optional new deadline of a stage. The approved estimate is
    never edited: when the customer approves the request, a new approved version is built from it (ТЗ 4.12).
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        PENDING = "pending", "На согласовании"
        APPROVED = "approved", "Одобрена"
        REJECTED = "rejected", "Отклонена"
        WITHDRAWN = "withdrawn", "Отозвана"

    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="change_requests")
    number = models.PositiveIntegerField()
    estimate = models.ForeignKey(Estimate, on_delete=models.PROTECT, related_name="change_requests")
    result_estimate = models.OneToOneField(Estimate, on_delete=models.PROTECT, null=True, blank=True, related_name="source_change")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    reason = models.TextField()
    schedule_stage = models.ForeignKey("repairs.Stage", on_delete=models.PROTECT, null=True, blank=True, related_name="change_requests")
    new_end_date = models.DateField(null=True, blank=True)
    old_end_date = models.DateField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_change_requests")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="submitted_change_requests")
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="decided_change_requests")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="unique_change_request_number"),
            models.CheckConstraint(condition=Q(status__in=["draft", "pending", "approved", "rejected", "withdrawn"]), name="change_request_status_valid"),
            models.CheckConstraint(condition=Q(schedule_stage__isnull=True) | Q(new_end_date__isnull=False), name="change_request_schedule_complete"),
        ]

    def __str__(self):
        return f"Изменение сметы №{self.number}"

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT

    @property
    def is_open(self):
        return self.status in (self.Status.DRAFT, self.Status.PENDING)

    @property
    def cost_delta(self):
        return money(sum((item.delta for item in self.items.all()), Decimal("0")))

    @property
    def new_total(self):
        return money(self.estimate.total + self.cost_delta)

    @property
    def current_end_date(self):
        """Deadline before the change: frozen when applied, otherwise the stage's current one."""
        if self.old_end_date:
            return self.old_end_date
        return self.schedule_stage.end_date if self.schedule_stage_id else None

    @property
    def days_delta(self):
        before = self.current_end_date
        return (self.new_end_date - before).days if (before and self.new_end_date) else None


class ChangeItem(models.Model):
    """One changed position: add a new line, change an existing one or remove it."""

    class Action(models.TextChoices):
        ADD = "add", "Добавить позицию"
        UPDATE = "update", "Изменить позицию"
        REMOVE = "remove", "Удалить позицию"

    request = models.ForeignKey(ChangeRequest, on_delete=models.CASCADE, related_name="items")
    action = models.CharField(max_length=8, choices=Action.choices)
    line = models.ForeignKey(EstimateLine, on_delete=models.PROTECT, null=True, blank=True, related_name="change_items")
    stage = models.ForeignKey("repairs.Stage", on_delete=models.PROTECT, related_name="change_items")
    name = models.CharField(max_length=255)
    cost_type = models.CharField(max_length=16, choices=CostType.choices, default=CostType.WORK)
    unit = models.CharField(max_length=20)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["request", "line"], name="one_change_per_line"),
            models.CheckConstraint(condition=Q(action__in=["add", "update", "remove"]), name="change_item_action_valid"),
            models.CheckConstraint(condition=Q(action="add", line__isnull=True) | (~Q(action="add") & Q(line__isnull=False)), name="change_item_line_matches_action"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="change_item_quantity_positive"),
            models.CheckConstraint(condition=Q(price__gte=0), name="change_item_price_not_negative"),
        ]

    @property
    def old_amount(self):
        return self.line.amount if self.line_id else Decimal("0.00")

    @property
    def new_amount(self):
        return Decimal("0.00") if self.action == self.Action.REMOVE else money(self.quantity * self.price)

    @property
    def delta(self):
        return money(self.new_amount - self.old_amount)

    @property
    def changed_fields(self):
        """Human labels of what an «update» changes: [(label, old, new)]."""
        if self.action != self.Action.UPDATE:
            return []
        labels = [("stage", "Этап"), ("name", "Наименование"), ("cost_type", "Вид затрат"), ("unit", "Ед. изм."),
                  ("quantity", "Количество"), ("price", "Цена")]
        display = lambda obj, field: (obj.get_cost_type_display() if field == "cost_type" else getattr(obj, field))
        return [(label, display(self.line, field), display(self, field)) for field, label in labels
                if getattr(self.line, field) != getattr(self, field)]


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
