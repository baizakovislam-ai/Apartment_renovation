"""Pages for expenses (FR12) and the financial information of the project (FR13). Closed to executors."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from apps.estimates.models import CostType
from apps.finances import services as expense_services
from apps.finances.models import Expense
from apps.projects.permissions import active_membership
from .finance_views import finance_project
from .forms import ExpenseForm
from .views import context, form_service_error, run_service

FILTERS = ["q", "status", "category", "stage", "date_from", "date_to"]
EDIT_FIELDS = ["amount", "date", "recipient", "purpose", "category", "stage", "note"]


def visible_expense(request, project, expense_id):
    expense = get_object_or_404(expense_services.visible_expenses(project, request.user).select_related(
        "stage", "created_by", "submitted_by", "decided_by"), pk=expense_id)
    return expense


@login_required
def finance_overview(request, project_id):
    project = finance_project(request, project_id)
    summary = expense_services.finance_summary(project)
    recent = expense_services.visible_expenses(project, request.user).select_related("stage", "created_by")[:6]
    pending = project.expenses.filter(status=Expense.Status.PENDING).select_related("created_by").order_by("submitted_at")
    return render(request, "workspace/finances.html", context(request, project, "finances", summary=summary, recent=recent,
                  pending=pending))


@login_required
def expense_list(request, project_id):
    project = finance_project(request, project_id)
    visible = expense_services.visible_expenses(project, request.user)
    filters = {key: request.GET.get(key, "") for key in FILTERS}
    expenses = list(expense_services.filter_expenses(visible, request.GET).select_related("stage", "created_by", "decided_by"))
    counts = {value: visible.filter(status=value).count() for value in Expense.Status.values}
    shown_total = sum((expense.amount for expense in expenses if expense.status == Expense.Status.CONFIRMED), 0)
    return render(request, "workspace/expenses.html", context(
        request, project, "expenses", expenses=expenses, filters=filters, counts=counts, total_count=sum(counts.values()),
        has_filters=any(value for key, value in filters.items() if key != "status"), shown_total=shown_total,
        categories=CostType.choices, statuses=Expense.Status.choices, stage_options=project.stages.all()))


@login_required
def expense_create(request, project_id):
    project = finance_project(request, project_id)
    membership = active_membership(project, request.user)
    initial = {"stage": request.GET["stage"]} if str(request.GET.get("stage", "")).isdigit() else {}
    form = ExpenseForm(request.POST or None, request.FILES or None, project=project, author_role=membership.role, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        submit, document, request_id = data.pop("send_now"), data.pop("document"), data.pop("request_id")
        try:
            expense = expense_services.create_expense(project=project, actor=request.user, submit=submit, document=document,
                                                      request_id=request_id, **data)
        except APIException as error:
            form_service_error(form, error)
        else:
            if expense.status == Expense.Status.PENDING:
                text = ("Расход добавлен и ждёт вашего подтверждения." if membership.role == "customer"
                        else "Расход отправлен заказчику на подтверждение.")
            else:
                text = "Черновик расхода сохранён. Его видите только вы — отправьте заказчику, когда будет готов."
            messages.success(request, text)
            return redirect("workspace:expense", project_id=project.pk, expense_id=expense.pk)
    return render(request, "workspace/form.html", context(
        request, project, "expenses", form=form, title="Новый расход", section="расхода", eyebrow="Деньги и согласования",
        subtitle="Укажите сумму, дату, кому и за что заплатили, и приложите чек. Расход учитывается в фактических затратах после подтверждения заказчиком.",
        cancel_url="workspace:expenses"))


@login_required
def expense_detail(request, project_id, expense_id):
    project = finance_project(request, project_id)
    expense = visible_expense(request, project, expense_id)
    membership = active_membership(project, request.user)
    editable = not project.is_archived
    events = project.events.filter(data__expense_id=expense.pk).select_related("actor")[:30]
    return render(request, "workspace/expense_detail.html", context(
        request, project, "expenses", expense=expense, events=events,
        is_author=expense_services.is_author(expense, request.user) and editable,
        can_decide=expense_services.can_decide(expense, membership) and editable))


@login_required
def expense_edit(request, project_id, expense_id):
    project = finance_project(request, project_id)
    expense = visible_expense(request, project, expense_id)
    if not (expense.is_editable and expense_services.is_author(expense, request.user)) or project.is_archived:
        raise PermissionDenied("Изменять можно только свой черновик расхода.")
    membership = active_membership(project, request.user)
    form = ExpenseForm(request.POST or None, request.FILES or None, project=project, expense=expense, author_role=membership.role)
    if request.method == "POST" and form.is_valid():
        data = {key: form.cleaned_data[key] for key in EDIT_FIELDS}
        try:
            expense_services.update_expense(expense=expense, actor=request.user, revision=form.cleaned_data["revision"],
                                            document=form.cleaned_data["document"],
                                            remove_document=form.cleaned_data.get("remove_document", False), **data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Расход сохранён.")
            return redirect("workspace:expense", project_id=project.pk, expense_id=expense.pk)
    return render(request, "workspace/form.html", context(
        request, project, "expenses", form=form, title=f"Расход №{expense.number}", section="расхода", eyebrow="Черновик расхода",
        subtitle="Пока расход черновик, его можно поправить или удалить.", cancel_href=reverse("workspace:expense", args=[project.pk, expense.pk])))


@login_required
@require_POST
def expense_action(request, project_id, expense_id):
    project = finance_project(request, project_id)
    expense = visible_expense(request, project, expense_id)
    action, revision = request.POST.get("action"), request.POST.get("revision")
    if action == "delete":
        if run_service(request, lambda: expense_services.delete_expense(expense=expense, actor=request.user, revision=revision),
                       f"Черновик расхода №{expense.number} удалён."):
            return redirect("workspace:expenses", project_id=project.pk)
        return redirect("workspace:expense", project_id=project.pk, expense_id=expense.pk)
    labels = {"submit": "Расход отправлен заказчику на подтверждение.", "withdraw": "Расход возвращён в черновик.",
              "confirm": "Расход подтверждён и учтён в фактических затратах.", "reject": "Расход отклонён. Автор увидит причину."}
    if action not in labels:
        raise Http404
    run_service(request, lambda: expense_services.expense_action(expense=expense, actor=request.user, action=action, revision=revision,
                reason=request.POST.get("reason", "")), labels[action])
    back = request.POST.get("next", "")
    if back.startswith(f"/projects/{project.pk}/") and "//" not in back:
        return redirect(back)
    return redirect("workspace:expense", project_id=project.pk, expense_id=expense.pk)
