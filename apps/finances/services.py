"""Expenses and financial information of the project (ТЗ FR12, FR13, UC «Добавление и учёт расходов»,
«Подтверждение или отклонение расходов», «Просмотр финансовой информации»).

Who does what:
  add, edit, delete a draft, send to the customer — the customer and the foreman (only their own expenses);
  confirm or reject (with a reason)                — the customer only;
  the executor has no access to money at all (ТЗ 4.1.1, прил. А).
Only confirmed expenses are actual costs; rejected ones are kept with the reason (ТЗ 4.12 «ранее учтённые расходы сохраняются»).
"""
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError

from apps.estimates import services as estimate_services
from apps.estimates.models import CostType, money
from apps.projects.models import ProjectMember
from apps.projects.permissions import require_customer, require_manager
from apps.projects.services import atomic_operation, lock_project, record_event
from apps.repairs.images import process_photo
from apps.repairs.models import Stage
from .models import Expense

STALE = "Расход уже изменил другой пользователь. Обновите страницу и повторите."
FIELDS = {"stage", "amount", "date", "recipient", "purpose", "category", "note"}
MAX_AMOUNT = Decimal("999999999999.99")
DOCUMENT_TYPES = "PDF, JPEG, PNG или WebP"


# ---------- access ----------

def require_finance_access(project, user):
    """Money is for the customer and the foreman only."""
    return require_manager(project, user)


def visible_expenses(project, user):
    """Everything sent to the customer, plus the user's own drafts (somebody else's draft is not ready yet)."""
    require_finance_access(project, user)
    return project.expenses.filter(~Q(status=Expense.Status.DRAFT) | Q(created_by=user))


def is_author(expense, user):
    return expense.created_by_id == getattr(user, "pk", None)


def can_decide(expense, member):
    return member is not None and member.role == ProjectMember.Role.CUSTOMER and expense.status == Expense.Status.PENDING


# ---------- checks ----------

def to_amount(value):
    try:
        number = Decimal(str(value).replace(",", ".").replace(" ", "")) if value not in (None, "") else None
    except InvalidOperation:
        number = None
    if number is None or not number.is_finite():
        raise ValidationError({"amount": "Укажите сумму числом."})
    if number <= 0:
        raise ValidationError({"amount": "Сумма должна быть больше нуля."})
    if number > MAX_AMOUNT:
        raise ValidationError({"amount": "Слишком большая сумма."})
    if number != number.quantize(Decimal("0.01")):
        raise ValidationError({"amount": "Сумма указывается с точностью до тыйына (два знака после запятой)."})
    return money(number)


def clean_expense(project, data, expense=None):
    unknown = set(data) - FIELDS
    if unknown:
        raise ValidationError(f"Неизвестные поля расхода: {', '.join(sorted(unknown))}.")
    cleaned = dict(data)
    creating = expense is None
    if "amount" in cleaned or creating:
        cleaned["amount"] = to_amount(cleaned.get("amount"))
    if "date" in cleaned or creating:
        day = cleaned.get("date")
        if isinstance(day, str):
            day = parse_date(day)
        if day is None:
            raise ValidationError({"date": "Укажите дату расхода."})
        if day > timezone.localdate():
            raise ValidationError({"date": "Дата расхода не может быть в будущем."})
        cleaned["date"] = day
    for field, label in (("recipient", "кому заплатили (получателя)"), ("purpose", "за что заплатили (назначение)")):
        if field in cleaned or creating:
            cleaned[field] = (cleaned.get(field) or "").strip()
            if not cleaned[field]:
                raise ValidationError({field: f"Укажите, {label}."})
    if "category" in cleaned or creating:
        cleaned["category"] = cleaned.get("category") or CostType.MATERIAL
        if cleaned["category"] not in CostType.values:
            raise ValidationError({"category": "Неизвестная категория расхода."})
    if "stage" in cleaned:
        stage = cleaned["stage"]
        if stage is not None:
            if stage.project_id != project.pk:
                raise ValidationError({"stage": "Этап должен относиться к этому проекту."})
            if stage.status == Stage.Status.CANCELLED:
                raise ValidationError({"stage": "Этап отменён — расход к нему отнести нельзя."})
    if "note" in cleaned:
        cleaned["note"] = (cleaned.get("note") or "").strip()
    return cleaned


def process_document(upload):
    """The supporting document (чек, накладная, акт): a PDF or a photo, checked by its content, not by its name."""
    name = getattr(upload, "name", "файл")
    if upload.size > settings.PHOTO_MAX_BYTES:
        raise ValidationError({"document": f"«{name}» больше {settings.PHOTO_MAX_BYTES // (1024 * 1024)} МБ."})
    upload.seek(0)
    head = upload.read(5)
    upload.seek(0)
    if head == b"%PDF-":
        data = upload.read()
        if b"%%EOF" not in data[-2048:]:
            raise ValidationError({"document": f"«{name}» — повреждённый PDF."})
        return ContentFile(data, name="document.pdf"), name[:255], "application/pdf", len(data)
    try:
        content, content_type, _width, _height, size = process_photo(upload)
    except ValidationError as error:
        raise ValidationError({"document": f"«{name}»: допустимы только {DOCUMENT_TYPES}."}) from error
    return content, name[:255], content_type, size


def attach_document(expense, upload):
    content, name, content_type, size = process_document(upload)
    old = expense.document.name if expense.document else ""
    expense.document.save(content.name, content, save=False)
    expense.document_name, expense.document_type, expense.document_size = name, content_type, size
    if old:
        transaction.on_commit(lambda: expense.document.storage.delete(old))


def lock_expense(expense):
    return Expense.objects.select_for_update().get(pk=expense.pk)


def check_revision(expense, revision):
    if revision in (None, ""):
        return
    try:
        current = int(revision)
    except (TypeError, ValueError):
        raise ValidationError({"revision": "Неверная версия расхода. Обновите страницу."})
    if current != expense.revision:
        raise ValidationError({"revision": STALE})


def own_expense(expense, actor, revision, action):
    """Lock the project and the expense; only its author may change it while it is a draft."""
    project = lock_project(expense.project)
    require_finance_access(project, actor)
    expense = lock_expense(expense)
    if not is_author(expense, actor):
        raise ValidationError(f"{action} может только автор расхода.")
    check_revision(expense, revision)
    return project, expense


# ---------- changes ----------

@atomic_operation
def create_expense(*, project, actor, submit=False, document=None, request_id=None, **data):
    project = lock_project(project)
    require_finance_access(project, actor)
    if request_id:
        existing = Expense.objects.filter(request_id=request_id).first()
        if existing:  # a repeated click must not create a second expense (ТЗ 4.13)
            if existing.project_id != project.pk or existing.created_by_id != actor.pk:
                raise ValidationError("Повторный идентификатор запроса.")
            return existing
    data = clean_expense(project, data)
    number = (project.expenses.aggregate(top=Max("number"))["top"] or 0) + 1
    extra = {"request_id": request_id} if request_id else {}
    expense = Expense(project=project, number=number, created_by=actor, **data, **extra)
    if document:
        attach_document(expense, document)
    if submit:
        expense.status = Expense.Status.PENDING
        expense.submitted_by, expense.submitted_at = actor, timezone.now()
    expense.save()
    record_event(project, actor, "expense.created", expense_id=expense.pk, number=number, amount=str(expense.amount),
                 purpose=expense.purpose, status=expense.status)
    return expense


@atomic_operation
def update_expense(*, expense, actor, revision=None, document=None, remove_document=False, **data):
    project, expense = own_expense(expense, actor, revision, "Изменить расход")
    if not expense.is_editable:
        raise ValidationError("Изменять можно только черновик расхода. Отзовите его с подтверждения, чтобы поправить.")
    data = clean_expense(project, data, expense)
    before = {key: str(getattr(expense, key)) for key in data if getattr(expense, key) != data[key]}
    for key, value in data.items():
        setattr(expense, key, value)
    if document:
        attach_document(expense, document)
        before["document"] = "заменён"
    elif remove_document and expense.document:
        old = expense.document.name
        expense.document, expense.document_name, expense.document_type, expense.document_size = "", "", "", 0
        transaction.on_commit(lambda: expense.document.storage.delete(old))
        before["document"] = "удалён"
    expense.revision += 1
    expense.save()
    if before:
        record_event(project, actor, "expense.updated", expense_id=expense.pk, number=expense.number, before=before,
                     after={key: str(getattr(expense, key)) for key in before if key != "document"})
    return expense


@atomic_operation
def delete_expense(*, expense, actor, revision=None):
    """Only one's own draft is deleted for good (ТЗ 4.12); anything sent to the customer stays in the list."""
    project, expense = own_expense(expense, actor, revision, "Удалить расход")
    if not expense.is_editable:
        raise ValidationError("Удалить можно только свой черновик. Отправленный расход сохраняется в истории.")
    number, purpose, amount = expense.number, expense.purpose, str(expense.amount)
    file_name, storage = expense.document.name, expense.document.storage
    expense.delete()
    if file_name:
        transaction.on_commit(lambda: storage.delete(file_name))
    record_event(project, actor, "expense.deleted", number=number, purpose=purpose, amount=amount)


@atomic_operation
def submit_expense(*, expense, actor, revision=None):
    project, expense = own_expense(expense, actor, revision, "Отправить расход на подтверждение")
    if expense.status != Expense.Status.DRAFT:
        raise ValidationError("На подтверждение отправляется только черновик.")
    expense.status = Expense.Status.PENDING
    expense.submitted_by, expense.submitted_at = actor, timezone.now()
    expense.revision += 1
    expense.save()
    record_event(project, actor, "expense.submitted", expense_id=expense.pk, number=expense.number, amount=str(expense.amount))
    return expense


@atomic_operation
def withdraw_expense(*, expense, actor, revision=None):
    """The author takes a pending expense back to correct it."""
    project, expense = own_expense(expense, actor, revision, "Отозвать расход")
    if expense.status != Expense.Status.PENDING:
        raise ValidationError("Отозвать можно только расход, который ждёт подтверждения.")
    expense.status = Expense.Status.DRAFT
    expense.submitted_by, expense.submitted_at = None, None
    expense.revision += 1
    expense.save()
    record_event(project, actor, "expense.withdrawn", expense_id=expense.pk, number=expense.number)
    return expense


def decided_expense(expense, actor, revision):
    project = lock_project(expense.project)
    require_customer(project, actor)
    expense = lock_expense(expense)
    check_revision(expense, revision)
    if expense.status != Expense.Status.PENDING:
        raise ValidationError(f"Решение уже принято: расход «{expense.get_status_display().lower()}»."
                              if expense.status in (Expense.Status.CONFIRMED, Expense.Status.REJECTED)
                              else "Расход ещё не отправлен на подтверждение.")
    return project, expense


@atomic_operation
def confirm_expense(*, expense, actor, revision=None):
    project, expense = decided_expense(expense, actor, revision)
    expense.status = Expense.Status.CONFIRMED
    expense.decided_by, expense.decided_at, expense.decision_reason = actor, timezone.now(), ""
    expense.revision += 1
    expense.save()
    record_event(project, actor, "expense.confirmed", expense_id=expense.pk, number=expense.number, amount=str(expense.amount))
    return expense


@atomic_operation
def reject_expense(*, expense, actor, reason, revision=None):
    reason = (reason or "").strip()
    project, expense = decided_expense(expense, actor, revision)
    if not reason:
        raise ValidationError({"reason": "Напишите причину отклонения."})
    expense.status = Expense.Status.REJECTED
    expense.decided_by, expense.decided_at, expense.decision_reason = actor, timezone.now(), reason
    expense.revision += 1
    expense.save()
    record_event(project, actor, "expense.rejected", expense_id=expense.pk, number=expense.number, reason=reason)
    return expense


ACTIONS = {"submit": submit_expense, "withdraw": withdraw_expense, "confirm": confirm_expense, "reject": reject_expense}


def expense_action(*, expense, actor, action, revision=None, reason=""):
    if action not in ACTIONS:
        raise ValidationError({"action": "Неизвестное действие."})
    extra = {"reason": reason} if action == "reject" else {}
    return ACTIONS[action](expense=expense, actor=actor, revision=revision, **extra)


# ---------- search and filters (FR17) ----------

def filter_expenses(expenses, params):
    """q — получатель, назначение, примечание; category, status, stage (or «none»), date_from, date_to, mine."""
    query = (params.get("q") or "").strip()
    if query:
        expenses = expenses.filter(Q(recipient__icontains=query) | Q(purpose__icontains=query) | Q(note__icontains=query))
    if params.get("category") in CostType.values:
        expenses = expenses.filter(category=params["category"])
    if params.get("status") in Expense.Status.values:
        expenses = expenses.filter(status=params["status"])
    stage = str(params.get("stage") or "")
    if stage == "none":
        expenses = expenses.filter(stage__isnull=True)
    elif stage.isdigit():
        expenses = expenses.filter(stage_id=stage)
    date_from, date_to = parse_date(params.get("date_from") or ""), parse_date(params.get("date_to") or "")
    if date_from:
        expenses = expenses.filter(date__gte=date_from)
    if date_to:
        expenses = expenses.filter(date__lte=date_to)
    return expenses


# ---------- financial information (FR13) ----------

def percent(part, whole):
    return round(float(part) * 100 / float(whole), 1) if whole else None


def finance_summary(project):
    """Budget, estimate cost, actual (confirmed) costs, what is left and how far the costs are from the estimate."""
    approved = estimate_services.approved_estimate(project)
    estimate_total = approved.total if approved else None
    expenses = list(project.expenses.exclude(status=Expense.Status.DRAFT).select_related("stage"))
    confirmed = [expense for expense in expenses if expense.status == Expense.Status.CONFIRMED]
    pending = [expense for expense in expenses if expense.status == Expense.Status.PENDING]
    spent = money(sum((expense.amount for expense in confirmed), Decimal("0")))
    pending_total = money(sum((expense.amount for expense in pending), Decimal("0")))
    budget = project.budget
    remaining = money(budget - spent) if budget is not None else None
    deviation = money(spent - estimate_total) if estimate_total is not None else None

    planned_by_type, planned_by_stage = {}, {}
    if approved:
        for line in approved.lines.select_related("stage"):
            planned_by_type[line.cost_type] = planned_by_type.get(line.cost_type, Decimal("0")) + line.amount
            planned_by_stage[line.stage] = planned_by_stage.get(line.stage, Decimal("0")) + line.amount
    actual_by_type, actual_by_stage = {}, {}
    for expense in confirmed:
        actual_by_type[expense.category] = actual_by_type.get(expense.category, Decimal("0")) + expense.amount
        actual_by_stage[expense.stage] = actual_by_stage.get(expense.stage, Decimal("0")) + expense.amount

    def row(label, planned, actual):
        planned, actual = money(planned), money(actual)
        return {"label": label, "planned": planned, "actual": actual, "delta": money(actual - planned),
                "used": percent(actual, planned), "over": actual > planned}

    by_category = [row(label, planned_by_type.get(value, 0), actual_by_type.get(value, 0))
                   for value, label in CostType.choices if value in planned_by_type or value in actual_by_type]
    stages = sorted({*planned_by_stage, *(stage for stage in actual_by_stage if stage is not None)}, key=lambda s: (s.position, s.pk))
    by_stage = [dict(row(stage.name, planned_by_stage.get(stage, 0), actual_by_stage.get(stage, 0)), stage=stage) for stage in stages]
    if None in actual_by_stage:
        by_stage.append(dict(row("Без привязки к этапу", 0, actual_by_stage[None]), stage=None))

    warnings = []
    if budget is not None and spent > budget:
        warnings.append(f"Подтверждённые расходы превысили лимит бюджета на {money(spent - budget):,.2f} сом.".replace(",", " "))
    elif budget is not None and spent + pending_total > budget:
        warnings.append("Если подтвердить все ожидающие расходы, лимит бюджета будет превышен.")
    if deviation is not None and deviation > 0:
        warnings.append(f"Расходы больше утверждённой сметы на {deviation:,.2f} сом.".replace(",", " "))
    for item in by_category:
        if item["over"] and item["planned"] > 0:
            warnings.append(f"Категория «{item['label']}»: потрачено больше, чем заложено в смете.")
    return {
        "budget": budget, "estimate": approved, "estimate_total": estimate_total, "spent": spent,
        "pending_total": pending_total, "pending_count": len(pending), "confirmed_count": len(confirmed),
        "rejected_count": sum(1 for expense in expenses if expense.status == Expense.Status.REJECTED),
        "remaining": remaining, "over_budget": remaining is not None and remaining < 0,
        "budget_used": percent(spent, budget), "deviation": deviation, "deviation_percent": percent(deviation, estimate_total) if deviation is not None else None,
        "estimate_used": percent(spent, estimate_total), "by_category": by_category, "by_stage": by_stage, "warnings": warnings,
    }
