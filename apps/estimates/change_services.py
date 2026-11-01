"""Changes of the approved estimate (ТЗ FR11) — Abdulazis's 60 % part.

A change request names:
  причина             — `reason`, required;
  изменяемая позиция  — an estimate line (or a new one) in `ChangeItem`;
  новое значение      — new stage / name / cost type / unit / quantity / price of that line;
  изменение стоимости — calculated: new amount minus old amount, per item and in total;
  изменение сроков    — optional new deadline of one stage (`schedule_stage`, `new_end_date`).

Statuses (ТЗ, прил. Б): Черновик → На согласовании → Одобрена / Отклонена / Отозвана.

Who does what (ТЗ 4.1.1, прил. А):
  prepare, submit, withdraw — the author: the foreman («предлагать изменения») or the customer;
  approve / reject          — the customer only («утверждать изменения сметы»); the foreman never approves;
  executor                  — no access, as to the whole estimate.

Approved data is never edited (ТЗ 4.12): approving a request builds a NEW approved estimate version from the old one
plus the changes, and the old version becomes «Заменена новой версией». The whole decision is one transaction
(ТЗ 4.13: атомарность согласований и финансовых изменений), a repeated click finds the request already decided.
"""
from datetime import date

from django.db.models import Max
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.projects.models import ProjectMember
from apps.projects.permissions import require_customer
from apps.projects.services import atomic_operation, lock_project, record_event
from apps.repairs.models import Stage
from .models import ChangeItem, ChangeRequest, Estimate, EstimateLine
from .services import approved_estimate, clean_line, lock_estimate, open_estimate, recalc, require_finance_access

STALE = "Заявку уже изменил другой пользователь. Обновите страницу и повторите."
VALUE_FIELDS = ("stage", "name", "cost_type", "unit", "quantity", "price")
CLOSED_STAGE_STATUSES = (Stage.Status.ACCEPTED, Stage.Status.CANCELLED)


# ---------- reading ----------

def open_requests(project):
    return project.change_requests.filter(status__in=[ChangeRequest.Status.DRAFT, ChangeRequest.Status.PENDING])


def pending_requests(project):
    return project.change_requests.filter(status=ChangeRequest.Status.PENDING)


def preview(request):
    """The estimate as it would look after the change: rows of (line or item, old amount, new amount, kind)."""
    items = {item.line_id: item for item in request.items.all() if item.line_id}
    rows = []
    for line in request.estimate.lines.select_related("stage"):
        item = items.get(line.pk)
        if item is None:
            rows.append({"kind": "same", "stage": line.stage, "name": line.name, "unit": line.unit, "quantity": line.quantity,
                         "price": line.price, "old": line.amount, "new": line.amount, "line": line, "item": None})
        else:
            rows.append({"kind": item.action, "stage": item.stage, "name": item.name, "unit": item.unit, "quantity": item.quantity,
                         "price": item.price, "old": item.old_amount, "new": item.new_amount, "line": line, "item": item})
    for item in request.items.filter(action=ChangeItem.Action.ADD).select_related("stage"):
        rows.append({"kind": "add", "stage": item.stage, "name": item.name, "unit": item.unit, "quantity": item.quantity,
                     "price": item.price, "old": item.old_amount, "new": item.new_amount, "line": None, "item": item})
    return rows


def budget_effect(request):
    """Budget limit, totals before / after and what is left of the limit (FR11 + FR13)."""
    budget = request.project.budget
    old_total, new_total = request.estimate.total, request.new_total
    return {"budget": budget, "old_total": old_total, "new_total": new_total, "delta": request.cost_delta,
            "left_before": None if budget is None else budget - old_total,
            "left_after": None if budget is None else budget - new_total,
            "over_budget": budget is not None and new_total > budget}


# ---------- helpers ----------

def lock_request(request):
    return ChangeRequest.objects.select_for_update().select_related("estimate").get(pk=request.pk)


def check_revision(request, revision):
    if revision in (None, "") or int(revision) != request.revision:
        raise ValidationError({"revision": STALE})


def bump(request, *fields):
    request.revision += 1
    request.save(update_fields=[*fields, "revision", "updated_at"])


def require_author(request, actor, verb):
    if request.created_by_id != actor.pk:
        raise PermissionDenied(f"{verb} заявку может только её автор.")


def editable_request(request, actor, revision):
    """Lock the project and the request for a change of a draft by its author."""
    project = lock_project(request.project)
    require_finance_access(project, actor)
    request = lock_request(request)
    require_author(request, actor, "Менять")
    if not request.is_editable:
        raise ValidationError("Заявку можно менять только в статусе «Черновик».")
    check_revision(request, revision)
    return project, request


def clean_schedule(project, stage, new_end_date):
    """Изменение сроков: a stage of this project that is still open and a deadline not before its start."""
    if stage is None and new_end_date in (None, ""):
        return None, None
    if stage is None or stage.project_id != project.pk:
        raise ValidationError({"schedule_stage": "Выберите этап этого проекта, срок которого меняется."})
    if stage.status in CLOSED_STAGE_STATUSES:
        raise ValidationError({"schedule_stage": f"Этап «{stage.name}» уже {stage.get_status_display().lower()}, его срок не меняется."})
    if new_end_date in (None, ""):
        raise ValidationError({"new_end_date": "Укажите новый срок окончания этапа."})
    if isinstance(new_end_date, str):
        try:
            new_end_date = date.fromisoformat(new_end_date)
        except ValueError:
            raise ValidationError({"new_end_date": "Укажите дату в формате ГГГГ-ММ-ДД."})
    if new_end_date < stage.start_date:
        raise ValidationError({"new_end_date": f"Срок не может быть раньше начала этапа ({stage.start_date:%d.%m.%Y})."})
    if new_end_date == stage.end_date:
        raise ValidationError({"new_end_date": "Новый срок совпадает с текущим."})
    return stage, new_end_date


# ---------- create, edit, delete a draft ----------

@atomic_operation
def create_request(*, project, actor, reason, schedule_stage=None, new_end_date=None):
    project = lock_project(project)
    member = require_finance_access(project, actor)
    base = approved_estimate(project)
    if base is None:
        raise ValidationError("Изменять можно только утверждённую смету. Сначала утвердите смету.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Укажите причину изменения сметы."})
    schedule_stage, new_end_date = clean_schedule(project, schedule_stage, new_end_date)
    number = (project.change_requests.aggregate(top=Max("number"))["top"] or 0) + 1
    request = ChangeRequest.objects.create(project=project, number=number, estimate=base, reason=reason, created_by=actor,
                                           schedule_stage=schedule_stage, new_end_date=new_end_date)
    record_event(project, actor, "estimate_change.created", change_id=request.pk, number=number, role=member.role, reason=reason)
    return request


@atomic_operation
def update_request(*, request, actor, revision, reason, schedule_stage=None, new_end_date=None):
    project, request = editable_request(request, actor, revision)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Укажите причину изменения сметы."})
    schedule_stage, new_end_date = clean_schedule(project, schedule_stage, new_end_date)
    request.reason, request.schedule_stage, request.new_end_date = reason, schedule_stage, new_end_date
    bump(request, "reason", "schedule_stage", "new_end_date")
    record_event(project, actor, "estimate_change.updated", change_id=request.pk, number=request.number)
    return request


@atomic_operation
def delete_request(*, request, actor):
    """ТЗ 4.12: only one's own draft may be deleted for good; a submitted request is withdrawn instead."""
    project = lock_project(request.project)
    require_finance_access(project, actor)
    request = lock_request(request)
    require_author(request, actor, "Удалить")
    if not request.is_editable:
        raise ValidationError("Удалить можно только черновик. Заявку на согласовании можно отозвать.")
    number = request.number
    request.delete()
    record_event(project, actor, "estimate_change.deleted", number=number)


# ---------- changed positions ----------

def merged_values(request, action, line, data):
    """New values of the position: for «update» the old line's values with the edited fields over them."""
    unknown = set(data) - set(VALUE_FIELDS)
    if unknown:
        raise ValidationError(f"Неизвестные поля позиции: {', '.join(sorted(unknown))}.")
    if action == ChangeItem.Action.ADD:
        if line is not None:
            raise ValidationError({"line": "Новая позиция не ссылается на строку сметы."})
        return clean_line(request.project, dict(data))
    if line is None or line.estimate_id != request.estimate_id:
        raise ValidationError({"line": "Выберите строку утверждённой сметы, которую меняет заявка."})
    old = {field: getattr(line, field) for field in VALUE_FIELDS}
    if action == ChangeItem.Action.REMOVE:
        return old
    values = {**old, **{key: value for key, value in data.items() if value not in (None, "")}}
    cleaned = clean_line(request.project, values, line)  # a line on a cancelled stage can only be removed
    cleaned = {field: cleaned.get(field, old[field]) for field in VALUE_FIELDS}
    if all(cleaned[field] == old[field] for field in VALUE_FIELDS):
        raise ValidationError("Новые значения совпадают с утверждёнными — изменять нечего.")
    return cleaned


@atomic_operation
def set_item(*, request, actor, revision, action, line=None, **data):
    """Add or replace the change of one position. One line of the estimate is changed at most once per request."""
    project, request = editable_request(request, actor, revision)
    if action not in ChangeItem.Action.values:
        raise ValidationError({"action": "Выберите: добавить, изменить или удалить позицию."})
    values = merged_values(request, action, line, data)
    item = request.items.filter(line=line).first() if line is not None else None
    if item is None:
        position = (request.items.aggregate(top=Max("position"))["top"] or 0) + 1
        item = ChangeItem.objects.create(request=request, action=action, line=line, position=position, **values)
    else:
        item.action = action
        for key, value in values.items():
            setattr(item, key, value)
        item.save()
    bump(request)
    record_event(project, actor, "estimate_change.item_set", change_id=request.pk, number=request.number, change=action,
                 name=item.name, delta=str(item.delta))
    return item


@atomic_operation
def delete_item(*, item, actor, revision):
    project, request = editable_request(item.request, actor, revision)
    name = item.name
    ChangeItem.objects.filter(pk=item.pk, request=request).delete()
    bump(request)
    record_event(project, actor, "estimate_change.item_deleted", change_id=request.pk, number=request.number, name=name)


# ---------- approval ----------

@atomic_operation
def submit_request(*, request, actor, revision=None):
    project = lock_project(request.project)
    require_finance_access(project, actor)
    request = lock_request(request)
    require_author(request, actor, "Отправить")
    if request.status != ChangeRequest.Status.DRAFT:
        raise ValidationError("На согласование отправляется только черновик.")
    if revision not in (None, ""):
        check_revision(request, revision)
    if not request.items.exists() and not request.schedule_stage_id:
        raise ValidationError("В заявке нет изменений: добавьте позицию или новый срок этапа.")
    if approved_estimate(project) != request.estimate:
        raise ValidationError("Смета уже изменилась: заявка составлена к старой версии. Создайте новую заявку.")
    request.status = ChangeRequest.Status.PENDING
    request.submitted_by, request.submitted_at = actor, timezone.now()
    request.decided_by, request.decided_at, request.decision_reason = None, None, ""
    bump(request, "status", "submitted_by", "submitted_at", "decided_by", "decided_at", "decision_reason")
    record_event(project, actor, "estimate_change.submitted", change_id=request.pk, number=request.number, delta=str(request.cost_delta))
    return request


@atomic_operation
def withdraw_request(*, request, actor):
    """The author takes the request back (Отозвана). It stays in the history; a new request can be made."""
    project = lock_project(request.project)
    require_finance_access(project, actor)
    request = lock_request(request)
    require_author(request, actor, "Отозвать")
    if request.status != ChangeRequest.Status.PENDING:
        raise ValidationError("Отозвать можно только заявку на согласовании. Черновик можно удалить.")
    request.status = ChangeRequest.Status.WITHDRAWN
    bump(request, "status")
    record_event(project, actor, "estimate_change.withdrawn", change_id=request.pk, number=request.number)
    return request


def decidable_request(request, actor):
    project = lock_project(request.project)
    require_customer(project, actor)  # the foreman never approves (ТЗ 4.1.1)
    request = lock_request(request)
    if request.status != ChangeRequest.Status.PENDING:
        raise ValidationError("Решение уже принято: заявка не на согласовании.")
    return project, request


@atomic_operation
def approve_request(*, request, actor):
    """Build the new approved estimate version from the old one plus the changes, in one transaction."""
    project, request = decidable_request(request, actor)
    base = lock_estimate(request.estimate)
    if approved_estimate(project) != base or base.status != Estimate.Status.APPROVED:
        raise ValidationError("Смета уже изменилась после подачи заявки. Отклоните её — автор подготовит новую.")
    if open_estimate(project):
        raise ValidationError("Сейчас готовится новая версия сметы (черновик или на согласовании). Сначала завершите её.")
    items = {item.line_id: item for item in request.items.select_related("stage") if item.line_id}
    added = list(request.items.filter(action=ChangeItem.Action.ADD).select_related("stage"))
    for item in [*items.values(), *added]:
        if item.action != ChangeItem.Action.REMOVE and item.stage.status == Stage.Status.CANCELLED:
            raise ValidationError(f"Этап «{item.stage.name}» отменён — позицию «{item.name}» туда отнести нельзя.")
    schedule_stage = None
    if request.schedule_stage_id:
        schedule_stage = Stage.objects.select_for_update().get(pk=request.schedule_stage_id)
        clean_schedule(project, schedule_stage, request.new_end_date)  # the stage may have closed meanwhile

    number = (project.estimates.aggregate(top=Max("number"))["top"] or 0) + 1
    now = timezone.now()
    base.status = Estimate.Status.SUPERSEDED
    base.revision += 1
    base.save(update_fields=["status", "revision", "updated_at"])
    new = Estimate.objects.create(
        project=project, number=number, based_on=base, status=Estimate.Status.APPROVED, created_by=request.created_by,
        comment=f"Изменение сметы №{request.number}: {request.reason}", submitted_by=request.submitted_by,
        submitted_at=request.submitted_at, decided_by=actor, decided_at=now)
    lines, position = [], 0
    for line in base.lines.all():
        item = items.get(line.pk)
        if item is not None and item.action == ChangeItem.Action.REMOVE:
            continue
        source = item or line
        position += 1
        lines.append(EstimateLine(estimate=new, stage_id=source.stage_id, name=source.name, cost_type=source.cost_type,
                                  unit=source.unit, quantity=source.quantity, price=source.price, position=position))
    for item in added:
        position += 1
        lines.append(EstimateLine(estimate=new, stage_id=item.stage_id, name=item.name, cost_type=item.cost_type,
                                  unit=item.unit, quantity=item.quantity, price=item.price, position=position))
    EstimateLine.objects.bulk_create(lines)
    recalc(new)

    if schedule_stage is not None:
        request.old_end_date = schedule_stage.end_date
        schedule_stage.end_date = request.new_end_date
        schedule_stage.save(update_fields=["end_date", "updated_at"])
        record_event(project, actor, "stage.updated", stage_id=schedule_stage.pk, name=schedule_stage.name,
                     end_date=str(request.new_end_date), reason=f"Изменение сметы №{request.number}")
    request.status = ChangeRequest.Status.APPROVED
    request.result_estimate = new
    request.decided_by, request.decided_at, request.decision_reason = actor, now, ""
    bump(request, "status", "result_estimate", "decided_by", "decided_at", "decision_reason", "old_end_date")
    record_event(project, actor, "estimate_change.approved", change_id=request.pk, number=request.number,
                 delta=str(new.total - base.total), estimate_id=new.pk, estimate_number=new.number, total=str(new.total))
    return request


@atomic_operation
def reject_request(*, request, actor, reason):
    project, request = decidable_request(request, actor)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Напишите, почему изменение не подходит."})
    request.status = ChangeRequest.Status.REJECTED
    request.decided_by, request.decided_at, request.decision_reason = actor, timezone.now(), reason
    bump(request, "status", "decided_by", "decided_at", "decision_reason")
    record_event(project, actor, "estimate_change.rejected", change_id=request.pk, number=request.number, reason=reason)
    return request


def is_author(request, user):
    return request.created_by_id == user.pk


def can_decide(request, membership):
    return request.status == ChangeRequest.Status.PENDING and membership is not None and membership.role == ProjectMember.Role.CUSTOMER
