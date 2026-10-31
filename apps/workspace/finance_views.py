"""Pages for the estimate, its approval and changes, works start, preliminary calculations, approvals journal and project search."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from apps.estimates import change_services
from apps.estimates import search as search_service
from apps.estimates import services as estimate_services
from apps.estimates.models import UNIT_SUGGESTIONS, ChangeItem, ChangeRequest, CostType, Estimate, EstimateLine
from apps.projects.permissions import active_membership, require_manager
from .forms import CalculationForm, CalculationItemForm, ChangeItemForm, ChangeRequestForm, EstimateLineForm
from .views import check_permission, context, form_service_error, own_project, run_service


def finance_project(request, project_id):
    """Estimate pages are closed to executors (ТЗ: исполнитель не видит смету и расходы)."""
    project = own_project(request, project_id)
    check_permission(require_manager, project, request.user)
    return project


def estimate_url(project, estimate=None):
    if estimate is None:
        return reverse("workspace:estimate", args=[project.pk])
    return reverse("workspace:estimate-version", args=[project.pk, estimate.pk])


# ---------- Estimate (FR09, FR10) ----------

@login_required
def estimate_page(request, project_id, estimate_id=None):
    project = finance_project(request, project_id)
    membership = active_membership(project, request.user)
    if estimate_id:
        estimate = get_object_or_404(project.estimates, pk=estimate_id)
    else:
        # The newest version is what people work on; a rejected one must stay visible with its reason.
        estimate = estimate_services.open_estimate(project) or estimate_services.latest_estimate(project)
    summary = estimate_services.estimate_summary(estimate) if estimate else None
    budget = project.budget
    difference = (budget - summary["total"]) if (summary and budget is not None) else None
    can_edit = bool(estimate and estimate.is_editable and not project.is_archived)
    line_form = EstimateLineForm(project=project, estimate=estimate) if can_edit else None
    return render(request, "workspace/estimate.html", context(
        request, project, "estimate", estimate=estimate, summary=summary, budget=budget, difference=difference,
        over_budget=difference is not None and difference < 0, can_edit=can_edit, line_form=line_form,
        versions=project.estimates.select_related("created_by", "decided_by"), unit_options=UNIT_SUGGESTIONS,
        open_estimate=estimate_services.open_estimate(project), approved=estimate_services.approved_estimate(project),
        start_blockers=estimate_services.start_blockers(project) if project.status == "planning" else [],
        is_foreman=membership.role == "foreman", calculations=project.calculations.all()[:5],
        open_changes=change_services.open_requests(project).select_related("created_by"),
        source_change=getattr(estimate, "source_change", None) if estimate else None))


@login_required
@require_POST
def estimate_create(request, project_id):
    project = finance_project(request, project_id)
    estimate = run_service(request, lambda: estimate_services.create_draft(project=project, actor=request.user),
                           "Создан черновик сметы. Добавьте строки и отправьте на согласование.")
    return redirect(estimate_url(project))


@login_required
@require_POST
def estimate_line_add(request, project_id, estimate_id):
    project = finance_project(request, project_id)
    estimate = get_object_or_404(project.estimates, pk=estimate_id)
    form = EstimateLineForm(request.POST, project=project, estimate=estimate)
    if form.is_valid():
        data = dict(form.cleaned_data)
        revision = data.pop("revision")
        run_service(request, lambda: estimate_services.add_line(estimate=estimate, actor=request.user, revision=revision, **data),
                    f"Строка «{data['name']}» добавлена.")
    else:
        messages.error(request, "Строка не добавлена: " + "; ".join(f"{form.fields[name].label}: {' '.join(errors)}" if name in form.fields else " ".join(errors)
                                                                     for name, errors in form.errors.items()))
    return redirect(estimate_url(project) + "#lines")


@login_required
def estimate_line_edit(request, project_id, line_id):
    project = finance_project(request, project_id)
    line = get_object_or_404(estimate_services.EstimateLine.objects.select_related("estimate"), pk=line_id, estimate__project=project)
    if not line.estimate.is_editable:
        messages.error(request, "Смету можно менять только в статусе «Черновик».")
        return redirect(estimate_url(project))
    form = EstimateLineForm(request.POST or None, project=project, estimate=line.estimate, line=line)
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        revision = data.pop("revision")
        try:
            estimate_services.update_line(line=line, actor=request.user, revision=revision, **data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Строка сметы сохранена.")
            return redirect(estimate_url(project) + "#lines")
    return render(request, "workspace/form.html", context(request, project, "estimate", form=form, title="Строка сметы",
                  section="строки", subtitle=f"Смета, версия {line.estimate.number}.", cancel_url="workspace:estimate",
                  unit_options=UNIT_SUGGESTIONS))


@login_required
@require_POST
def estimate_line_delete(request, project_id, line_id):
    project = finance_project(request, project_id)
    line = get_object_or_404(estimate_services.EstimateLine, pk=line_id, estimate__project=project)
    run_service(request, lambda: estimate_services.delete_line(line=line, actor=request.user, revision=request.POST.get("revision")), "Строка удалена.")
    return redirect(estimate_url(project) + "#lines")


@login_required
@require_POST
def estimate_action(request, project_id, estimate_id):
    project = finance_project(request, project_id)
    estimate = get_object_or_404(project.estimates, pk=estimate_id)
    action = request.POST.get("action")
    calls = {
        "submit": (lambda: estimate_services.submit_estimate(estimate=estimate, actor=request.user, comment=request.POST.get("comment", "")),
                   "Смета отправлена заказчику на согласование."),
        "withdraw": (lambda: estimate_services.withdraw_estimate(estimate=estimate, actor=request.user), "Смета возвращена в черновик."),
        "approve": (lambda: estimate_services.approve_estimate(estimate=estimate, actor=request.user), "Смета утверждена."),
        "reject": (lambda: estimate_services.reject_estimate(estimate=estimate, actor=request.user, reason=request.POST.get("reason", "")),
                   "Смета отклонена. Прораб увидит причину и подготовит новую версию."),
    }
    if action not in calls:
        raise Http404
    run_service(request, *calls[action])
    return redirect(estimate_url(project))


@login_required
@require_POST
def start_works(request, project_id):
    project = finance_project(request, project_id)
    run_service(request, lambda: estimate_services.start_works(project=project, actor=request.user),
                "Работы запущены. Теперь прораб может начинать этапы, а исполнители — задачи.")
    return redirect(request.POST.get("next") or estimate_url(project))


# ---------- Changes of the approved estimate (FR11) ----------

def change_url(project, change, anchor=""):
    return reverse("workspace:estimate-change", args=[project.pk, change.pk]) + anchor


def form_errors(form):
    return "; ".join(f"{form.fields[name].label}: {' '.join(errors)}" if name in form.fields else " ".join(errors)
                     for name, errors in form.errors.items())


@login_required
def change_list(request, project_id):
    project = finance_project(request, project_id)
    approved = estimate_services.approved_estimate(project)
    form = ChangeRequestForm(request.POST or None, project=project)
    if request.method == "POST":
        if form.is_valid():
            data = form.cleaned_data
            try:
                change = change_services.create_request(project=project, actor=request.user, reason=data["reason"],
                                                        schedule_stage=data["schedule_stage"], new_end_date=data["new_end_date"])
            except APIException as error:
                form_service_error(form, error)
            else:
                messages.success(request, f"Заявка №{change.number} создана. Отметьте, какие позиции меняются, и отправьте на согласование.")
                return redirect(change_url(project, change))
    changes = list(project.change_requests.select_related("created_by", "decided_by", "estimate", "result_estimate", "schedule_stage")
                   .prefetch_related("items__line"))
    status = request.GET.get("status", "")
    if status in ChangeRequest.Status.values:
        shown = [change for change in changes if change.status == status]
    else:
        status, shown = "", changes
    return render(request, "workspace/estimate_changes.html", context(
        request, project, "estimate-changes", approved=approved, form=form, changes=shown, status=status,
        statuses=ChangeRequest.Status.choices, counts={value: sum(1 for c in changes if c.status == value) for value in ChangeRequest.Status.values},
        open_estimate=estimate_services.open_estimate(project)))


@login_required
def change_detail(request, project_id, change_id):
    project = finance_project(request, project_id)
    membership = active_membership(project, request.user)
    change = get_object_or_404(project.change_requests.select_related("estimate", "created_by", "submitted_by", "decided_by",
                                                                       "result_estimate", "schedule_stage"), pk=change_id)
    can_edit = change.is_editable and change_services.is_author(change, request.user) and not project.is_archived
    approved = estimate_services.approved_estimate(project)
    return render(request, "workspace/estimate_change.html", context(
        request, project, "estimate-changes", change=change, rows=change_services.preview(change),
        effect=change_services.budget_effect(change), can_edit=can_edit,
        can_decide=change_services.can_decide(change, membership) and not project.is_archived,
        is_author=change_services.is_author(change, request.user), is_stale=change.is_open and approved != change.estimate,
        add_form=ChangeItemForm(change=change) if can_edit else None,
        edit_form=ChangeRequestForm(project=project, change=change) if can_edit else None,
        items=list(change.items.select_related("line", "stage")), unit_options=UNIT_SUGGESTIONS))


@login_required
@require_POST
def change_action(request, project_id, change_id):
    project = finance_project(request, project_id)
    change = get_object_or_404(project.change_requests, pk=change_id)
    action, post, user = request.POST.get("action"), request.POST, request.user
    revision = post.get("revision")
    if action == "update":
        form = ChangeRequestForm(post, project=project, change=change)
        if form.is_valid():
            data = form.cleaned_data
            run_service(request, lambda: change_services.update_request(
                request=change, actor=user, revision=revision, reason=data["reason"], schedule_stage=data["schedule_stage"],
                new_end_date=data["new_end_date"]), "Заявка сохранена.")
        else:
            messages.error(request, "Заявка не сохранена: " + form_errors(form))
        return redirect(change_url(project, change))
    if action == "add_item":
        form = ChangeItemForm(post, change=change)
        if form.is_valid():
            data = dict(form.cleaned_data)
            data.pop("revision")
            run_service(request, lambda: change_services.set_item(request=change, actor=user, revision=revision, action="add", **data),
                        f"Новая позиция «{data['name']}» добавлена в заявку.")
        else:
            messages.error(request, "Позиция не добавлена: " + form_errors(form))
        return redirect(change_url(project, change, "#changes"))
    if action == "remove_line":
        line = get_object_or_404(EstimateLine, pk=post.get("line") or 0, estimate=change.estimate)
        run_service(request, lambda: change_services.set_item(request=change, actor=user, revision=revision, action="remove", line=line),
                    f"Позиция «{line.name}» будет удалена из сметы.")
        return redirect(change_url(project, change, "#changes"))
    if action == "delete_item":
        item = get_object_or_404(ChangeItem, pk=post.get("item") or 0, request=change)
        run_service(request, lambda: change_services.delete_item(item=item, actor=user, revision=revision), "Изменение позиции отменено.")
        return redirect(change_url(project, change, "#changes"))
    if action == "delete":
        if run_service(request, lambda: change_services.delete_request(request=change, actor=user), f"Черновик заявки №{change.number} удалён."):
            return redirect("workspace:estimate-changes", project_id=project.pk)
        return redirect(change_url(project, change))
    calls = {
        "submit": (lambda: change_services.submit_request(request=change, actor=user, revision=revision),
                   "Заявка отправлена заказчику на согласование."),
        "withdraw": (lambda: change_services.withdraw_request(request=change, actor=user), "Заявка отозвана."),
        "approve": (lambda: change_services.approve_request(request=change, actor=user),
                    "Изменение одобрено: создана новая утверждённая версия сметы."),
        "reject": (lambda: change_services.reject_request(request=change, actor=user, reason=post.get("reason", "")),
                   "Изменение отклонено. Автор увидит причину."),
    }
    if action not in calls:
        raise Http404
    run_service(request, *calls[action])
    return redirect(change_url(project, change))


@login_required
def change_line_edit(request, project_id, change_id, line_id):
    """New values for one line of the approved estimate (an «update» item of the request)."""
    project = finance_project(request, project_id)
    change = get_object_or_404(project.change_requests.select_related("estimate"), pk=change_id)
    line = get_object_or_404(EstimateLine.objects.select_related("stage"), pk=line_id, estimate=change.estimate)
    if not (change.is_editable and change_services.is_author(change, request.user)):
        messages.error(request, "Менять позиции можно только в своём черновике заявки.")
        return redirect(change_url(project, change))
    current = change.items.filter(line=line, action=ChangeItem.Action.UPDATE).first()
    form = ChangeItemForm(request.POST or None, change=change, source=current or line)
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        revision = data.pop("revision")
        try:
            change_services.set_item(request=change, actor=request.user, revision=revision, action="update", line=line, **data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, f"Новые значения позиции «{line.name}» записаны в заявку.")
            return redirect(change_url(project, change, "#changes"))
    return render(request, "workspace/form.html", context(
        request, project, "estimate-changes", form=form, title=f"Изменить позицию «{line.name}»", section="позиции",
        subtitle=(f"Сейчас в смете: {line.quantity.normalize():f} {line.unit} × {line.price} сом = {line.amount} сом. "
                  f"Укажите новые значения — разница в стоимости посчитается сама."),
        cancel_url="workspace:estimate-changes", unit_options=UNIT_SUGGESTIONS))


# ---------- Preliminary calculation (FR16) ----------

@login_required
def calculation_list(request, project_id):
    project = finance_project(request, project_id)
    form = CalculationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            calculation = estimate_services.create_calculation(project=project, actor=request.user, **form.cleaned_data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Расчёт создан. Добавьте работы с объёмом и ценой.")
            return redirect("workspace:calculation", project_id=project.pk, calculation_id=calculation.pk)
    calculations = list(project.calculations.prefetch_related("items").select_related("created_by"))
    return render(request, "workspace/calculations.html", context(request, project, "calculations", calculations=calculations, form=form))


@login_required
def calculation_detail(request, project_id, calculation_id):
    project = finance_project(request, project_id)
    calculation = get_object_or_404(project.calculations.select_related("created_by"), pk=calculation_id)
    items = list(calculation.items.all())
    open_estimate = estimate_services.open_estimate(project)
    by_type = {}
    for item in items:
        by_type[item.get_cost_type_display()] = by_type.get(item.get_cost_type_display(), Decimal("0")) + item.amount
    return render(request, "workspace/calculation_detail.html", context(
        request, project, "calculations", calculation=calculation, items=items, item_form=CalculationItemForm(),
        edit_form=CalculationForm(initial={"name": calculation.name, "note": calculation.note}), unit_options=UNIT_SUGGESTIONS,
        stages=project.stages.exclude(status="cancelled"), open_estimate=open_estimate,
        can_transfer=open_estimate is None or open_estimate.is_editable,
        by_type=sorted(by_type.items(), key=lambda pair: -pair[1]), per_m2=(calculation.total / project.area) if project.area else None))


@login_required
@require_POST
def calculation_update(request, project_id, calculation_id):
    project = finance_project(request, project_id)
    calculation = get_object_or_404(project.calculations, pk=calculation_id)
    action = request.POST.get("action")
    if action == "delete":
        if run_service(request, lambda: estimate_services.delete_calculation(calculation=calculation, actor=request.user), "Расчёт удалён."):
            return redirect("workspace:calculations", project_id=project.pk)
    elif action == "rename":
        run_service(request, lambda: estimate_services.update_calculation(calculation=calculation, actor=request.user,
                    name=request.POST.get("name", ""), note=request.POST.get("note", "")), "Расчёт сохранён.")
    elif action == "add":
        form = CalculationItemForm(request.POST)
        if form.is_valid():
            run_service(request, lambda: estimate_services.add_item(calculation=calculation, actor=request.user, **form.cleaned_data),
                        f"Добавлено: {form.cleaned_data['work']}.")
        else:
            messages.error(request, "Позиция не добавлена: " + "; ".join(f"{form.fields[name].label}: {' '.join(errors)}" for name, errors in form.errors.items() if name in form.fields))
    elif action == "to_estimate":
        stage = project.stages.filter(pk=request.POST.get("stage") or 0).first()
        if run_service(request, lambda: estimate_services.calculation_to_estimate(calculation=calculation, actor=request.user, stage=stage),
                       "Позиции расчёта перенесены в черновик сметы. Проверьте этапы и цены."):
            return redirect(estimate_url(project) + "#lines")
    else:
        raise Http404
    return redirect("workspace:calculation", project_id=project.pk, calculation_id=calculation.pk)


@login_required
def calculation_item_edit(request, project_id, item_id):
    project = finance_project(request, project_id)
    item = get_object_or_404(estimate_services.CalculationItem.objects.select_related("calculation"), pk=item_id, calculation__project=project)
    form = CalculationItemForm(request.POST or None, item=item)
    if request.method == "POST" and form.is_valid():
        try:
            estimate_services.update_item(item=item, actor=request.user, **form.cleaned_data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Позиция сохранена.")
            return redirect("workspace:calculation", project_id=project.pk, calculation_id=item.calculation_id)
    return render(request, "workspace/form.html", context(request, project, "calculations", form=form, title="Позиция расчёта",
                  section="позиции", subtitle=f"Расчёт «{item.calculation.name}».", cancel_url="workspace:calculations",
                  unit_options=UNIT_SUGGESTIONS))


@login_required
@require_POST
def calculation_item_delete(request, project_id, item_id):
    project = finance_project(request, project_id)
    item = get_object_or_404(estimate_services.CalculationItem, pk=item_id, calculation__project=project)
    calculation_id = item.calculation_id
    run_service(request, lambda: estimate_services.delete_item(item=item, actor=request.user), "Позиция удалена.")
    return redirect("workspace:calculation", project_id=project.pk, calculation_id=calculation_id)


# ---------- Search and filtering (FR17) ----------

@login_required
def approvals(request, project_id):
    project = finance_project(request, project_id)
    filters = {key: request.GET.get(key, "") for key in ["q", "type", "status", "author", "date_from", "date_to"]}
    items = search_service.approval_items(project, request.user, request.GET)
    pending = [item for item in search_service.approval_items(project, request.user, {}) if item["status"] == "pending"]
    return render(request, "workspace/approvals.html", context(
        request, project, "approvals", items=items, filters=filters, has_filters=any(filters.values()), pending_count=len(pending),
        approval_types=search_service.APPROVAL_TYPES, approval_statuses=search_service.APPROVAL_STATUSES,
        people=project.members.select_related("user")))


@login_required
def project_search(request, project_id):
    project = own_project(request, project_id)
    query = request.GET.get("q", "").strip()
    results = search_service.project_search(project, request.user, query)
    return render(request, "workspace/search.html", context(request, project, "search", query=query, results=results))
