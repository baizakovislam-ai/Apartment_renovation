from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from apps.projects.permissions import require_customer, require_manager
from apps.quality import services as quality
from apps.reports import services as reports
from apps.reports.models import ReportPhoto
from apps.repairs import task_services as tasks
from apps.repairs.models import Task
from .views import check_permission, context, flatten_errors, form_service_error, own_project
from .work_forms import AcceptanceForm, PhotoReportForm, RemarkActionForm, RemarkForm, TaskActionForm, TaskForm


@login_required
def task_list(request, project_id):
    project = own_project(request, project_id)
    items = tasks.visible_tasks(project, request.user)
    return render(request, "workspace/tasks.html", context(request, project, "tasks", tasks=items, progress=tasks.project_progress(project)))


@login_required
def task_form(request, project_id, task_id=None):
    project = own_project(request, project_id)
    check_permission(require_manager, project, request.user)
    task = get_object_or_404(project.tasks, pk=task_id) if task_id else None
    form = TaskForm(request.POST or None, project=project, task=task)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        version = data.pop("version")
        if task:
            data.pop("request_id")
        try:
            if task:
                task = tasks.update_task(task=task, actor=request.user, version=version, **data)
            else:
                task = tasks.create_task(project=project, actor=request.user, **data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Задача сохранена.")
            return redirect("workspace:task-detail", project_id=project.pk, task_id=task.pk)
    return render(request, "workspace/work_form.html", context(request, project, "tasks", form=form, title="Редактирование задачи" if task else "Новая задача", subtitle="Выберите этап, исполнителя и срок выполнения.", back_section="tasks"))


@login_required
def task_detail(request, project_id, task_id):
    project = own_project(request, project_id)
    task = get_object_or_404(tasks.visible_tasks(project, request.user), pk=task_id)
    form = PhotoReportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            reports.create_report(task=task, actor=request.user, **form.cleaned_data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Фотоотчёт добавлен.")
            return redirect("workspace:task-detail", project_id=project.pk, task_id=task.pk)
    return render(request, "workspace/task_detail.html", context(request, project, "tasks", task=task, form=form,
        reports=task.photo_reports.select_related("author").prefetch_related("photos"),
        task_events=project.events.filter(data__task_id=task.pk).select_related("actor")[:30]))


@login_required
@require_POST
def task_action(request, project_id, task_id):
    project = own_project(request, project_id)
    task = get_object_or_404(tasks.visible_tasks(project, request.user), pk=task_id)
    form = TaskActionForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        try:
            if data["action"] == "delete":
                tasks.delete_task(task=task, actor=request.user, version=data["version"])
                messages.success(request, "Черновик задачи удалён.")
                return redirect("workspace:tasks", project_id=project.pk)
            tasks.transition_task(task=task, actor=request.user, **data)
        except APIException as error:
            messages.error(request, flatten_errors(error.detail))
        else:
            messages.success(request, "Статус задачи обновлён.")
    else:
        messages.error(request, "Некорректное действие. Обновите страницу и повторите.")
    return redirect("workspace:task-detail", project_id=project.pk, task_id=task.pk)


@login_required
def photo(request, photo_id):
    photo = get_object_or_404(ReportPhoto.objects.select_related("report__task__project"), pk=photo_id)
    task = photo.report.task
    if not task.project.members.filter(user=request.user).exists():
        raise Http404
    if not tasks.visible_tasks(task.project, request.user).filter(pk=task.pk).exists():
        raise Http404
    try:
        response = FileResponse(photo.image.open("rb"), content_type="image/png" if photo.image.name.endswith(".png") else "image/jpeg")
    except FileNotFoundError:
        raise Http404("Фотография не найдена.")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def report_list(request, project_id):
    project = own_project(request, project_id)
    items = project.photo_reports.filter(task__in=tasks.visible_tasks(project, request.user)).select_related("task__stage", "author").prefetch_related("photos")
    return render(request, "workspace/photo_reports.html", context(request, project, "reports", reports=items))


@login_required
def acceptance(request, project_id):
    project = own_project(request, project_id)
    check_permission(require_manager, project, request.user)
    stages = project.stages.annotate(task_total=Count("tasks", distinct=True), task_done=Count("tasks", filter=Q(tasks__status="done"), distinct=True), open_remarks=Count("remarks", filter=~Q(remarks__status="closed"), distinct=True)).prefetch_related("decisions__actor")
    return render(request, "workspace/acceptance.html", context(request, project, "acceptance", stages=stages, progress=tasks.project_progress(project)))


@login_required
@require_POST
def acceptance_action(request, project_id, stage_id):
    project = own_project(request, project_id)
    check_permission(require_manager, project, request.user)
    stage = get_object_or_404(project.stages, pk=stage_id)
    form = AcceptanceForm(request.POST)
    if form.is_valid():
        try:
            quality.stage_action(stage=stage, actor=request.user, **form.cleaned_data)
        except APIException as error:
            messages.error(request, flatten_errors(error.detail))
        else:
            messages.success(request, "Состояние приёмки обновлено.")
    else:
        messages.error(request, "Некорректное действие. Обновите страницу.")
    return redirect("workspace:acceptance", project_id=project.pk)


@login_required
def remarks(request, project_id):
    project = own_project(request, project_id)
    return render(request, "workspace/remarks.html", context(request, project, "remarks", remarks=quality.visible_remarks(project, request.user)))


@login_required
def remark_create(request, project_id):
    project = own_project(request, project_id)
    check_permission(require_customer, project, request.user)
    form = RemarkForm(request.POST or None, project=project)
    if request.method == "POST" and form.is_valid():
        try:
            quality.create_remark(project=project, actor=request.user, **form.cleaned_data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Замечание создано. Для приёмки нужно подтвердить его устранение.")
            return redirect("workspace:remarks", project_id=project.pk)
    return render(request, "workspace/work_form.html", context(request, project, "remarks", form=form, title="Новое замечание", subtitle="Опишите дефект, назначьте ответственного и срок устранения.", back_section="remarks"))


@login_required
@require_POST
def remark_action(request, project_id, remark_id):
    project = own_project(request, project_id)
    remark = get_object_or_404(quality.visible_remarks(project, request.user), pk=remark_id)
    form = RemarkActionForm(request.POST)
    if form.is_valid():
        try:
            quality.remark_action(remark=remark, actor=request.user, **form.cleaned_data)
        except APIException as error:
            messages.error(request, flatten_errors(error.detail))
        else:
            messages.success(request, "Замечание обновлено.")
    else:
        messages.error(request, "Некорректное действие. Обновите страницу.")
    return redirect("workspace:remarks", project_id=project.pk)
