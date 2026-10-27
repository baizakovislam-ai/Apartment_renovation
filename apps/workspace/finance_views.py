"""Pages for the estimate, its approval, works start, preliminary calculations, approvals journal and project search."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from apps.estimates import search as search_service
from apps.estimates import services as estimate_services
from apps.estimates.models import UNIT_SUGGESTIONS, CostType, Estimate
from apps.projects.permissions import active_membership, require_manager
from .forms import CalculationForm, CalculationItemForm, EstimateLineForm
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
        is_foreman=membership.role == "foreman", calculations=project.calculations.all()[:5]))


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
