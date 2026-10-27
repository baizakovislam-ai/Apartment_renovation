"""Estimate, its approval, work start and preliminary calculations (ТЗ FR09, FR10, FR16, scenario 4.2).

Who does what:
  draft lines        — customer and foreman (FR09);
  send for approval  — foreman only (FR10);
  approve / reject   — customer only; the foreman never approves his own request (ТЗ 4.1.1);
  start works        — customer, after the foreman joined and the estimate is approved (ТЗ 4.2).
The executor has no access to the estimate or money at all (ТЗ 4.1.1, прил. А).
"""
from decimal import Decimal, InvalidOperation

from django.db.models import Max
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.projects.models import Project, ProjectMember
from apps.projects.permissions import require_customer, require_manager, require_member
from apps.projects.services import atomic_operation, lock_project, record_event
from apps.repairs.models import Stage
from .models import Calculation, CalculationItem, CostType, Estimate, EstimateLine, money

STALE = "Смету уже изменил другой пользователь. Обновите страницу и повторите."
LINE_FIELDS = {"stage", "name", "cost_type", "unit", "quantity", "price"}
ITEM_FIELDS = {"work", "cost_type", "unit", "volume", "price"}


# ---------- reading ----------

def require_finance_access(project, user):
    """Estimates and money are for the customer and the foreman only."""
    return require_manager(project, user)


def open_estimate(project):
    return project.estimates.filter(status__in=[Estimate.Status.DRAFT, Estimate.Status.PENDING]).first()


def approved_estimate(project):
    return project.estimates.filter(status=Estimate.Status.APPROVED).first()


def latest_estimate(project):
    return project.estimates.first()


def estimate_summary(estimate):
    """Totals by stage and by cost type for the estimate page."""
    lines = list(estimate.lines.select_related("stage"))
    by_stage, by_type = {}, {}
    for line in lines:
        by_stage.setdefault(line.stage, Decimal("0"))
        by_stage[line.stage] += line.amount
        by_type.setdefault(line.get_cost_type_display(), Decimal("0"))
        by_type[line.get_cost_type_display()] += line.amount
    total = money(sum((line.amount for line in lines), Decimal("0")))
    groups = [{"stage": stage, "lines": [line for line in lines if line.stage_id == stage.pk], "total": money(stage_total)}
              for stage, stage_total in by_stage.items()]
    share = lambda value: round(float(value) * 100 / float(total)) if total else 0
    return {"lines": lines, "groups": groups, "by_stage": [(stage, money(value)) for stage, value in by_stage.items()],
            "by_type": sorted(((name, money(value), share(value)) for name, value in by_type.items()), key=lambda item: -item[1]),
            "total": total}


def lock_estimate(estimate):
    return Estimate.objects.select_for_update().get(pk=estimate.pk)


def check_revision(estimate, revision):
    if revision in (None, "") or int(revision) != estimate.revision:
        raise ValidationError({"revision": STALE})


def recalc(estimate):
    estimate.total = money(sum((line.amount for line in estimate.lines.all()), Decimal("0")))
    estimate.revision += 1
    estimate.save(update_fields=["total", "revision", "updated_at"])


def to_decimal(value, field, label):
    try:
        number = Decimal(str(value).replace(",", ".").replace(" ", "")) if value not in (None, "") else None
    except InvalidOperation:
        number = None
    if number is None or not number.is_finite():
        raise ValidationError({field: f"Укажите {label} числом."})
    return number


# ---------- estimate versions ----------

@atomic_operation
def create_draft(*, project, actor):
    """Start a new version. Lines are copied from the latest version so nothing is typed twice."""
    project = lock_project(project)
    require_finance_access(project, actor)
    if open_estimate(project):
        raise ValidationError("Уже есть смета в работе (черновик или на согласовании).")
    previous = latest_estimate(project)
    number = (project.estimates.aggregate(top=Max("number"))["top"] or 0) + 1
    estimate = Estimate.objects.create(project=project, number=number, based_on=previous, created_by=actor)
    if previous:
        EstimateLine.objects.bulk_create([
            EstimateLine(estimate=estimate, stage_id=line.stage_id, name=line.name, cost_type=line.cost_type, unit=line.unit,
                         quantity=line.quantity, price=line.price, position=line.position)
            for line in previous.lines.all() if line.stage.status != Stage.Status.CANCELLED])
        recalc(estimate)
    record_event(project, actor, "estimate.created", estimate_id=estimate.pk, number=number)
    return estimate


def clean_line(project, data, line=None):
    unknown = set(data) - LINE_FIELDS
    if unknown:
        raise ValidationError(f"Неизвестные поля строки сметы: {', '.join(sorted(unknown))}.")
    cleaned = dict(data)
    stage = cleaned.get("stage", line.stage if line else None)
    if stage is None or stage.project_id != project.pk:
        raise ValidationError({"stage": "Выберите этап этого проекта."})
    if stage.status == Stage.Status.CANCELLED:
        raise ValidationError({"stage": "Этап отменён."})
    if "name" in cleaned or line is None:
        cleaned["name"] = (cleaned.get("name") or "").strip()
        if not cleaned["name"]:
            raise ValidationError({"name": "Укажите наименование."})
    if "unit" in cleaned or line is None:
        cleaned["unit"] = (cleaned.get("unit") or "").strip()
        if not cleaned["unit"]:
            raise ValidationError({"unit": "Укажите единицу измерения."})
    if "cost_type" in cleaned and cleaned["cost_type"] not in CostType.values:
        raise ValidationError({"cost_type": "Неизвестный вид затрат."})
    if "quantity" in cleaned or line is None:
        cleaned["quantity"] = to_decimal(cleaned.get("quantity"), "quantity", "количество")
        if cleaned["quantity"] <= 0:
            raise ValidationError({"quantity": "Количество должно быть больше нуля."})
    if "price" in cleaned or line is None:
        cleaned["price"] = to_decimal(cleaned.get("price"), "price", "цену")
        if cleaned["price"] < 0:
            raise ValidationError({"price": "Цена не может быть отрицательной."})
    return cleaned


def editable_estimate(estimate, actor, revision):
    project = lock_project(estimate.project)
    require_finance_access(project, actor)
    estimate = lock_estimate(estimate)
    if not estimate.is_editable:
        raise ValidationError("Смету можно менять только в статусе «Черновик».")
    check_revision(estimate, revision)
    return project, estimate


@atomic_operation
def add_line(*, estimate, actor, revision, **data):
    project, estimate = editable_estimate(estimate, actor, revision)
    data = clean_line(project, data)
    position = (estimate.lines.aggregate(top=Max("position"))["top"] or 0) + 1
    line = EstimateLine.objects.create(estimate=estimate, position=position, **data)
    recalc(estimate)
    record_event(project, actor, "estimate.line_added", estimate_id=estimate.pk, name=line.name, amount=str(line.amount))
    return line


@atomic_operation
def update_line(*, line, actor, revision, **data):
    project, estimate = editable_estimate(line.estimate, actor, revision)
    line = EstimateLine.objects.select_for_update().get(pk=line.pk)
    data = clean_line(project, data, line)
    before = {key: str(getattr(line, key)) for key in data}
    for key, value in data.items():
        setattr(line, key, value)
    line.save()
    recalc(estimate)
    record_event(project, actor, "estimate.line_updated", estimate_id=estimate.pk, name=line.name, before=before,
                 after={key: str(getattr(line, key)) for key in data})
    return line


@atomic_operation
def delete_line(*, line, actor, revision):
    project, estimate = editable_estimate(line.estimate, actor, revision)
    name = line.name
    EstimateLine.objects.filter(pk=line.pk).delete()
    recalc(estimate)
    record_event(project, actor, "estimate.line_deleted", estimate_id=estimate.pk, name=name)


@atomic_operation
def submit_estimate(*, estimate, actor, comment=""):
    project = lock_project(estimate.project)
    require_member(project, actor, [ProjectMember.Role.FOREMAN])
    estimate = lock_estimate(estimate)
    if estimate.status != Estimate.Status.DRAFT:
        raise ValidationError("На согласование отправляется только черновик.")
    if not estimate.lines.exists():
        raise ValidationError("В смете нет ни одной строки.")
    estimate.status = Estimate.Status.PENDING
    estimate.comment = (comment or "").strip()
    estimate.submitted_by, estimate.submitted_at = actor, timezone.now()
    estimate.decided_by, estimate.decided_at, estimate.decision_reason = None, None, ""
    estimate.revision += 1
    estimate.save()
    record_event(project, actor, "estimate.submitted", estimate_id=estimate.pk, number=estimate.number, total=str(estimate.total))
    return estimate


@atomic_operation
def withdraw_estimate(*, estimate, actor):
    """The foreman takes a pending estimate back to fix it."""
    project = lock_project(estimate.project)
    require_member(project, actor, [ProjectMember.Role.FOREMAN])
    estimate = lock_estimate(estimate)
    if estimate.status != Estimate.Status.PENDING:
        raise ValidationError("Отозвать можно только смету на согласовании.")
    estimate.status = Estimate.Status.DRAFT
    estimate.revision += 1
    estimate.save()
    record_event(project, actor, "estimate.withdrawn", estimate_id=estimate.pk, number=estimate.number)
    return estimate


@atomic_operation
def approve_estimate(*, estimate, actor):
    project = lock_project(estimate.project)
    require_customer(project, actor)
    estimate = lock_estimate(estimate)
    if estimate.status != Estimate.Status.PENDING:
        raise ValidationError("Утвердить можно только смету на согласовании.")
    project.estimates.filter(status=Estimate.Status.APPROVED).update(status=Estimate.Status.SUPERSEDED)
    estimate.status = Estimate.Status.APPROVED
    estimate.decided_by, estimate.decided_at, estimate.decision_reason = actor, timezone.now(), ""
    estimate.revision += 1
    estimate.save()
    record_event(project, actor, "estimate.approved", estimate_id=estimate.pk, number=estimate.number, total=str(estimate.total))
    return estimate


@atomic_operation
def reject_estimate(*, estimate, actor, reason):
    project = lock_project(estimate.project)
    require_customer(project, actor)
    estimate = lock_estimate(estimate)
    reason = (reason or "").strip()
    if estimate.status != Estimate.Status.PENDING:
        raise ValidationError("Отклонить можно только смету на согласовании.")
    if not reason:
        raise ValidationError({"reason": "Напишите, что нужно исправить."})
    estimate.status = Estimate.Status.REJECTED
    estimate.decided_by, estimate.decided_at, estimate.decision_reason = actor, timezone.now(), reason
    estimate.revision += 1
    estimate.save()
    record_event(project, actor, "estimate.rejected", estimate_id=estimate.pk, number=estimate.number, reason=reason)
    return estimate


def start_blockers(project):
    reasons = []
    if not project.members.filter(is_active=True, role=ProjectMember.Role.FOREMAN).exists():
        reasons.append("в проекте нет прораба (он должен принять приглашение)")
    if not approved_estimate(project):
        reasons.append("смета ещё не утверждена")
    if not project.stages.exclude(status=Stage.Status.CANCELLED).exists():
        reasons.append("нет ни одного этапа")
    return reasons


@atomic_operation
def start_works(*, project, actor):
    """Scenario 4.2: the customer starts works after the foreman joined and the estimate is approved."""
    project = lock_project(project)
    require_customer(project, actor)
    if project.status != Project.Status.PLANNING:
        raise ValidationError("Работы по проекту уже запущены.")
    blockers = start_blockers(project)
    if blockers:
        raise ValidationError("Запустить работы нельзя: " + "; ".join(blockers) + ".")
    project.status = Project.Status.IN_PROGRESS
    project.save(update_fields=["status", "updated_at"])
    record_event(project, actor, "project.started", name=project.name)
    return project


# ---------- preliminary calculation (FR16) ----------

def clean_item(data, item=None):
    unknown = set(data) - ITEM_FIELDS
    if unknown:
        raise ValidationError(f"Неизвестные поля: {', '.join(sorted(unknown))}.")
    cleaned = dict(data)
    for field, label in (("work", "работу"), ("unit", "единицу измерения")):
        if field in cleaned or item is None:
            cleaned[field] = (cleaned.get(field) or "").strip()
            if not cleaned[field]:
                raise ValidationError({field: f"Укажите {label}."})
    if "cost_type" in cleaned and cleaned["cost_type"] not in CostType.values:
        raise ValidationError({"cost_type": "Неизвестный вид затрат."})
    if "volume" in cleaned or item is None:
        cleaned["volume"] = to_decimal(cleaned.get("volume"), "volume", "объём")
        if cleaned["volume"] <= 0:
            raise ValidationError({"volume": "Объём должен быть больше нуля."})
    if "price" in cleaned or item is None:
        cleaned["price"] = to_decimal(cleaned.get("price"), "price", "цену за единицу")
        if cleaned["price"] < 0:
            raise ValidationError({"price": "Цена не может быть отрицательной."})
    return cleaned


def editable_calculation(calculation, actor):
    project = lock_project(calculation.project)
    require_finance_access(project, actor)
    return project, Calculation.objects.select_for_update().get(pk=calculation.pk)


@atomic_operation
def create_calculation(*, project, actor, name, note=""):
    project = lock_project(project)
    require_finance_access(project, actor)
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "Назовите расчёт, например «Эконом-вариант»."})
    calculation = Calculation.objects.create(project=project, name=name, note=(note or "").strip(), created_by=actor)
    record_event(project, actor, "calculation.created", calculation_id=calculation.pk, name=name)
    return calculation


@atomic_operation
def update_calculation(*, calculation, actor, name, note=""):
    project, calculation = editable_calculation(calculation, actor)
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "Укажите название расчёта."})
    calculation.name, calculation.note = name, (note or "").strip()
    calculation.save()
    return calculation


@atomic_operation
def delete_calculation(*, calculation, actor):
    """A calculation is a working draft, not approved data, so it may be deleted."""
    project, calculation = editable_calculation(calculation, actor)
    name = calculation.name
    calculation.delete()
    record_event(project, actor, "calculation.deleted", name=name)


@atomic_operation
def add_item(*, calculation, actor, **data):
    project, calculation = editable_calculation(calculation, actor)
    data = clean_item(data)
    position = (calculation.items.aggregate(top=Max("position"))["top"] or 0) + 1
    item = CalculationItem.objects.create(calculation=calculation, position=position, **data)
    calculation.save(update_fields=["updated_at"])
    return item


@atomic_operation
def update_item(*, item, actor, **data):
    project, calculation = editable_calculation(item.calculation, actor)
    item = CalculationItem.objects.select_for_update().get(pk=item.pk)
    for key, value in clean_item(data, item).items():
        setattr(item, key, value)
    item.save()
    calculation.save(update_fields=["updated_at"])
    return item


@atomic_operation
def delete_item(*, item, actor):
    project, calculation = editable_calculation(item.calculation, actor)
    CalculationItem.objects.filter(pk=item.pk).delete()
    calculation.save(update_fields=["updated_at"])


@atomic_operation
def calculation_to_estimate(*, calculation, actor, stage):
    """«Предварительный расчёт может использоваться при создании сметы»: copy every item into the draft estimate."""
    project = lock_project(calculation.project)
    require_finance_access(project, actor)
    calculation = Calculation.objects.get(pk=calculation.pk)
    items = list(calculation.items.all())
    if not items:
        raise ValidationError("В расчёте нет позиций.")
    if stage is None or stage.project_id != project.pk or stage.status == Stage.Status.CANCELLED:
        raise ValidationError({"stage": "Выберите этап этого проекта, к которому отнести позиции."})
    estimate = open_estimate(project)
    if estimate is None:
        estimate = create_draft(project=project, actor=actor)
    estimate = lock_estimate(estimate)
    if not estimate.is_editable:
        raise ValidationError("Смета сейчас на согласовании. Дождитесь решения заказчика или отзовите её.")
    position = estimate.lines.aggregate(top=Max("position"))["top"] or 0
    EstimateLine.objects.bulk_create([
        EstimateLine(estimate=estimate, stage=stage, name=item.work, cost_type=item.cost_type, unit=item.unit,
                     quantity=item.volume, price=item.price, position=position + index)
        for index, item in enumerate(items, 1)])
    recalc(estimate)
    record_event(project, actor, "calculation.to_estimate", calculation_id=calculation.pk, name=calculation.name,
                 estimate_id=estimate.pk, lines=len(items))
    return estimate

