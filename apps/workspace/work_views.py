"""Pages for the 40% milestone: project card, tasks, photo reports, acceptance and remarks."""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from apps.projects import services as project_services
from apps.projects.permissions import require_customer, require_manager
from apps.quality import services as quality_services
from apps.quality.models import Remark
from apps.quality.views import filter_remarks
from apps.repairs import services as stage_services
from apps.repairs import task_services
from apps.repairs.models import Photo, Task
from apps.repairs.views import filter_tasks
from .forms import ProjectForm, RemarkForm, TaskForm
from .views import check_permission, context, flatten_errors, form_service_error, own_project


def service_message(request, call, success):
    """Run a service call and turn its outcome into a flash message."""
    try:
        result = call()
    except APIException as error:
        messages.error(request, flatten_errors(error.detail))
        return None
    messages.success(request, success)
    return result if result is not None else True


# ---------- Project card (FR02) ----------

@login_required
def project_detail(request, project_id):
    project = own_project(request, project_id)
    progress = task_services.project_progress(project)
    return render(request, "workspace/project_detail.html", context(request, project, "project", progress=progress))


@login_required
def project_edit(request, project_id):
    project = own_project(request, project_id)
    check_permission(require_customer, project, request.user)
    form = ProjectForm(request.POST or None, project=project)
    if request.method == "POST" and form.is_valid():
        try:
            project_services.update_project(project=project, actor=request.user, **form.cleaned_data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Данные проекта сохранены.")
            return redirect("workspace:project", project_id=project.pk)
    return render(request, "workspace/form.html", context(request, project, "project", form=form, title="Редактирование проекта",
                  subtitle="Адрес, площадь, сроки и бюджет ремонта.", cancel_url="workspace:project", section="проекта"))


@login_required
@require_POST
def project_delete(request, project_id):
    project = own_project(request, project_id)
    if service_message(request, lambda: project_services.delete_project(project=project, actor=request.user), "Черновик проекта удалён."):
        return redirect("workspace:projects")
    return redirect("workspace:project", project_id=project.pk)


# ---------- Tasks and deadlines (FR05) ----------

@login_required
def task_list(request, project_id):
    project = own_project(request, project_id)
    tasks = filter_tasks(task_services.visible_tasks(project, request.user), request.GET)
    filters = {key: request.GET.get(key, "") for key in ["q", "status", "stage", "deadline"]}
    return render(request, "workspace/tasks.html", context(request, project, "tasks", tasks=tasks, filters=filters,
                  task_statuses=Task.Status.choices, stage_options=project.stages.all(), has_filters=any(filters.values())))


@login_required
def task_form(request, project_id, task_id=None):
    project = own_project(request, project_id)
    check_permission(require_manager, project, request.user)
    task = get_object_or_404(project.tasks.select_related("stage"), pk=task_id) if task_id else None
    if task and task.status == "done":
        messages.error(request, "Выполненную задачу изменить нельзя.")
        return redirect("workspace:task", project_id=project.pk, task_id=task.pk)
    form = TaskForm(request.POST or None, project=project, task=task)
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        version, request_id = data.pop("version"), data.pop("request_id")
        if task and task.status != "todo":
            data.pop("assignee", None)
        try:
            if task:
                task = task_services.update_task(task=task, actor=request.user, version=version, **data)
            else:
                task = task_services.create_task(project=project, actor=request.user, request_id=request_id, **data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Задача сохранена.")
            return redirect("workspace:task", project_id=project.pk, task_id=task.pk)
    return render(request, "workspace/form.html", context(request, project, "tasks", form=form,
                  title="Редактирование задачи" if task else "Новая задача", section="задачи",
                  subtitle="Назначьте исполнителя и срок в пределах этапа.", cancel_url="workspace:tasks"))


@login_required
@require_POST
def task_delete(request, project_id, task_id):
    project = own_project(request, project_id)
    task = get_object_or_404(project.tasks, pk=task_id)
    if service_message(request, lambda: task_services.delete_task(task=task, actor=request.user, version=request.POST.get("version")), "Задача удалена."):
        return redirect("workspace:tasks", project_id=project.pk)
    return redirect("workspace:task", project_id=project.pk, task_id=task.pk)


def visible_task(request, project, task_id):
    task = get_object_or_404(project.tasks.select_related("stage", "assignee__user", "created_by"), pk=task_id)
    try:
        task_services.require_task_access(task, request.user)
    except APIException:
        raise Http404
    return task


@login_required
def task_detail(request, project_id, task_id):
    project = own_project(request, project_id)
    task = visible_task(request, project, task_id)
    member = project.members.get(user=request.user)
    reports = task.reports.select_related("author").prefetch_related("photos")
    events = project.events.filter(data__task_id=task.pk).select_related("actor")[:30]
    return render(request, "workspace/task_detail.html", context(request, project, "tasks", task=task, reports=reports, events=events,
                  is_assignee=task.assignee_id == member.pk, remarks=task.remarks.select_related("responsible__user"),
                  report_request_id=uuid.uuid4()))


@login_required
@require_POST
def task_action(request, project_id, task_id):
    project = own_project(request, project_id)
    task = visible_task(request, project, task_id)
    labels = {"start": "Задача взята в работу.", "submit": "Задача передана на проверку.", "approve": "Выполнение подтверждено.", "return": "Задача возвращена на доработку."}
    action = request.POST.get("action")
    if action not in labels:
        raise Http404
    service_message(request, lambda: task_services.task_action(task=task, actor=request.user, action=action,
                    version=request.POST.get("version"), text=request.POST.get("text", "")), labels[action])
    return redirect("workspace:task", project_id=project.pk, task_id=task.pk)


@login_required
@require_POST
def photo_report_create(request, project_id, task_id):
    project = own_project(request, project_id)
    task = visible_task(request, project, task_id)
    service_message(request, lambda: task_services.add_photo_report(task=task, actor=request.user, description=request.POST.get("description", ""),
                    images=request.FILES.getlist("images"), request_id=request.POST.get("request_id") or None), "Фотоотчёт опубликован.")
    return redirect(reverse("workspace:task", args=[project.pk, task.pk]) + "#reports")


# ---------- Stage start and acceptance (FR07) ----------

@login_required
@require_POST
def stage_start(request, project_id, stage_id):
    project = own_project(request, project_id)
    stage = get_object_or_404(project.stages, pk=stage_id)
    service_message(request, lambda: stage_services.update_stage(stage=stage, actor=request.user, status="in_progress"), f"Работы по этапу «{stage.name}» начаты.")
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("workspace:stages", project_id=project.pk)


@login_required
def acceptance(request, project_id):
    project = own_project(request, project_id)
    check_permission(require_manager, project, request.user)
    stages = []
    for stage in project.stages.exclude(status="cancelled").select_related("responsible__user"):
        tasks = list(stage.tasks.all())
        stages.append({
            "stage": stage, "blockers": quality_services.acceptance_blockers(stage),
            "tasks_done": sum(task.status == "done" for task in tasks), "tasks_total": len(tasks),
            "remarks": stage.remarks.filter(status__in=quality_services.UNRESOLVED).select_related("responsible__user"),
            "photos": Photo.objects.filter(report__task__stage=stage).order_by("-id")[:6],
            "decisions": stage.decisions.select_related("actor")[:5],
            "percent": task_services.stage_progress(stage),
        })
    return render(request, "workspace/acceptance.html", context(request, project, "acceptance", stage_rows=stages))


@login_required
@require_POST
def stage_acceptance_action(request, project_id, stage_id):
    project = own_project(request, project_id)
    stage = get_object_or_404(project.stages, pk=stage_id)
    action = request.POST.get("action")
    calls = {
        "submit": (lambda: quality_services.submit_stage(stage=stage, actor=request.user), "Этап передан заказчику на приёмку."),
        "accept": (lambda: quality_services.accept_stage(stage=stage, actor=request.user), "Этап принят."),
        "return": (lambda: quality_services.return_stage(stage=stage, actor=request.user, reason=request.POST.get("reason", "")), "Этап возвращён на доработку."),
    }
    if action not in calls:
        raise Http404
    service_message(request, *calls[action])
    return redirect("workspace:acceptance", project_id=project.pk)


# ---------- Remarks (FR07) ----------

@login_required
def remarks(request, project_id):
    project = own_project(request, project_id)
    membership = project.members.get(user=request.user)
    form = RemarkForm(request.POST or None, project=project, initial={"stage": request.GET.get("stage")}) if membership.role == "customer" else None
    if request.method == "POST":
        check_permission(require_customer, project, request.user)
        if form.is_valid():
            try:
                quality_services.create_remark(project=project, actor=request.user, **form.cleaned_data)
            except APIException as error:
                form_service_error(form, error)
            else:
                messages.success(request, "Замечание создано. Ответственный увидит его в своём списке.")
                return redirect("workspace:remarks", project_id=project.pk)
    items = filter_remarks(quality_services.visible_remarks(project, request.user), request.GET).prefetch_related("comments__author")
    filters = {key: request.GET.get(key, "") for key in ["q", "status", "stage"]}
    return render(request, "workspace/remarks.html", context(request, project, "remarks", remarks=items, form=form, filters=filters,
                  remark_statuses=Remark.Status.choices, stage_options=project.stages.all(), has_filters=any(filters.values())))


@login_required
@require_POST
def remark_action(request, project_id, remark_id):
    project = own_project(request, project_id)
    remark = get_object_or_404(project.remarks, pk=remark_id)
    labels = {"reply": "Ответ добавлен.", "fix": "Отмечено как устранённое. Заказчик проверит результат.", "close": "Замечание закрыто.", "reopen": "Замечание возвращено на доработку."}
    action = request.POST.get("action")
    if action not in labels:
        raise Http404
    service_message(request, lambda: quality_services.remark_action(remark=remark, actor=request.user, action=action,
                    version=request.POST.get("version"), text=request.POST.get("text", "")), labels[action])
    return redirect(reverse("workspace:remarks", args=[project.pk]) + f"#remark-{remark.pk}")
