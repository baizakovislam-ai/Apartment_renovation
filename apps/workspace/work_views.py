"""Pages for the project card, tasks, photo reports, acceptance and remarks."""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from apps.projects import services as project_services
from apps.projects.permissions import active_membership, require_customer, require_manager
from apps.quality import services as quality_services
from apps.quality.models import Remark
from apps.quality.views import filter_remarks
from apps.repairs import services as stage_services
from apps.repairs import task_services
from apps.repairs.models import Photo, Task
from apps.repairs.views import filter_tasks
from .forms import ProjectForm, RemarkEditForm, RemarkForm, TaskForm
from .views import check_permission, context, form_service_error, own_project, run_service

# Kept for older imports.
service_message = run_service


# ---------- Project card (FR02) ----------

@login_required
def project_detail(request, project_id):
    project = own_project(request, project_id)
    progress = task_services.project_progress(project)
    membership = active_membership(project, request.user)
    next_steps = None
    if project.status == "planning" and not project.is_archived and membership.role in ("customer", "foreman"):
        members = project.members.filter(is_active=True)
        next_steps = [
            ("Пригласить прораба", members.filter(role="foreman").exists() or project.invitations.filter(status="pending", role="foreman").exists(), reverse("workspace:members", args=[project.pk])),
            ("Добавить этапы ремонта", project.stages.exists(), reverse("workspace:stage-create", args=[project.pk])),
            ("Пригласить исполнителей", members.filter(role="executor").exists(), reverse("workspace:members", args=[project.pk])),
            ("Составить смету по этапам", project.estimates.filter(lines__isnull=False).exists(), reverse("workspace:estimate", args=[project.pk])),
            ("Прораб отправляет смету, заказчик утверждает", project.estimates.filter(status__in=["approved", "superseded"]).exists(), reverse("workspace:estimate", args=[project.pk])),
            ("Запустить работы (кнопка на странице сметы)", project.status != "planning", reverse("workspace:estimate", args=[project.pk])),
            ("Создать задачи с исполнителями и сроками", project.tasks.exists(), reverse("workspace:task-create", args=[project.pk])),
        ]
    is_owner = membership.role == "customer" and project.created_by_id == request.user.pk
    return render(request, "workspace/project_detail.html", context(
        request, project, "project", progress=progress, next_steps=next_steps, is_owner=is_owner,
        delete_blockers=project_services.project_delete_blockers(project) if is_owner else [],
        other_members=project.members.filter(is_active=True).exclude(user=request.user).count(),
        pending_invitations=project.invitations.filter(status="pending").count()))


@login_required
def project_edit(request, project_id):
    project = own_project(request, project_id)
    check_permission(require_customer, project, request.user)
    if project.is_archived:
        messages.error(request, project_services.ARCHIVED_MESSAGE)
        return redirect("workspace:project", project_id=project.pk)
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
    name = project.name
    if run_service(request, lambda: project_services.delete_project(project=project, actor=request.user), f"Проект «{name}» удалён."):
        request.session.pop("current_project_id", None)
        return redirect("workspace:projects")
    return redirect("workspace:project", project_id=project.pk)


@login_required
@require_POST
def project_archive(request, project_id):
    project = own_project(request, project_id)
    if request.POST.get("action") == "restore":
        run_service(request, lambda: project_services.restore_project(project=project, actor=request.user), "Проект возвращён из архива.")
        return redirect("workspace:project", project_id=project.pk)
    if run_service(request, lambda: project_services.archive_project(project=project, actor=request.user, reason=request.POST.get("reason", "")),
                   f"Проект «{project.name}» отправлен в архив. Его можно найти в «Мои проекты» → «Архив»."):
        request.session.pop("current_project_id", None)
        return redirect("workspace:projects")
    return redirect("workspace:project", project_id=project.pk)


# ---------- Tasks and deadlines (FR05) ----------

@login_required
def task_list(request, project_id):
    project = own_project(request, project_id)
    tasks = filter_tasks(task_services.visible_tasks(project, request.user), request.GET)
    filters = {key: request.GET.get(key, "") for key in ["q", "status", "stage", "assignee", "deadline", "due_from", "due_to", "sort"]}
    return render(request, "workspace/tasks.html", context(
        request, project, "tasks", tasks=tasks, filters=filters, task_statuses=Task.Status.choices, stage_options=project.stages.all(),
        executors=project.members.filter(role="executor").select_related("user"),
        deadline_options=[("open", "Только открытые"), ("soon", "Скоро срок (2 дня)"), ("overdue", "Просроченные")],
        sort_options=[("", "Сначала ближайший срок"), ("-due", "Сначала дальний срок"), ("new", "Сначала новые"), ("status", "По статусу"), ("name", "По названию")],
        has_filters=any(value for key, value in filters.items() if key != "sort")))


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
    if run_service(request, lambda: task_services.delete_task(task=task, actor=request.user, version=request.POST.get("version")), "Задача удалена."):
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
    member = active_membership(project, request.user)
    reports = task.reports.select_related("author").prefetch_related("photos")
    events = project.events.filter(data__task_id=task.pk).select_related("actor")[:30]
    remark_stage_open = task.stage.status in ("in_progress", "completed", "pending_acceptance")
    return render(request, "workspace/task_detail.html", context(request, project, "tasks", task=task, reports=reports, events=events,
                  is_assignee=task.assignee_id == member.pk, remarks=task.remarks.select_related("responsible__user"),
                  report_request_id=uuid.uuid4(), remark_stage_open=remark_stage_open))


@login_required
@require_POST
def task_action(request, project_id, task_id):
    project = own_project(request, project_id)
    task = visible_task(request, project, task_id)
    labels = {"start": "Задача взята в работу.", "submit": "Задача передана на проверку.", "approve": "Выполнение подтверждено.", "return": "Задача возвращена на доработку."}
    action = request.POST.get("action")
    if action not in labels:
        raise Http404
    run_service(request, lambda: task_services.task_action(task=task, actor=request.user, action=action,
                version=request.POST.get("version"), text=request.POST.get("text", "")), labels[action])
    return redirect("workspace:task", project_id=project.pk, task_id=task.pk)


@login_required
@require_POST
def photo_report_create(request, project_id, task_id):
    project = own_project(request, project_id)
    task = visible_task(request, project, task_id)
    run_service(request, lambda: task_services.add_photo_report(task=task, actor=request.user, description=request.POST.get("description", ""),
                images=request.FILES.getlist("images"), request_id=request.POST.get("request_id") or None), "Фотоотчёт опубликован.")
    return redirect(reverse("workspace:task", args=[project.pk, task.pk]) + "#reports")


# ---------- Stage start and acceptance (FR07) ----------

@login_required
@require_POST
def stage_start(request, project_id, stage_id):
    project = own_project(request, project_id)
    stage = get_object_or_404(project.stages, pk=stage_id)
    run_service(request, lambda: stage_services.update_stage(stage=stage, actor=request.user, status="in_progress"), f"Работы по этапу «{stage.name}» начаты.")
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
    run_service(request, *calls[action])
    return redirect(reverse("workspace:acceptance", args=[project.pk]) + f"#stage-{stage.pk}")


# ---------- Remarks (FR07) ----------

def visible_remark(request, project, remark_id):
    remark = get_object_or_404(project.remarks.select_related("stage", "task", "responsible__user", "created_by", "closed_by"), pk=remark_id)
    try:
        quality_services.require_remark_access(remark, request.user)
    except APIException:
        raise Http404
    return remark


@login_required
def remarks(request, project_id):
    project = own_project(request, project_id)
    items = filter_remarks(quality_services.visible_remarks(project, request.user), request.GET, request.user)
    items = items.prefetch_related("photos").order_by("status", "due_date", "id")
    filters = {key: request.GET.get(key, "") for key in ["q", "status", "stage", "mine", "deadline"]}
    all_remarks = quality_services.visible_remarks(project, request.user)
    counts = {"open": all_remarks.filter(status="open").count(), "fixed": all_remarks.filter(status="fixed").count(),
              "closed": all_remarks.filter(status="closed").count()}
    return render(request, "workspace/remarks.html", context(request, project, "remarks", remarks=items, filters=filters, counts=counts,
                  remark_statuses=Remark.Status.choices, stage_options=project.stages.all(), has_filters=any(filters.values())))


@login_required
def remark_create(request, project_id):
    project = own_project(request, project_id)
    check_permission(require_manager, project, request.user)
    membership = active_membership(project, request.user)
    initial = {key: request.GET.get(key) for key in ["stage", "task"] if request.GET.get(key)}
    form = RemarkForm(request.POST or None, request.FILES or None, project=project, author_role=membership.role, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            remark = quality_services.create_remark(project=project, actor=request.user, **form.cleaned_data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, f"Замечание создано. {remark.responsible.user.get_full_name() or remark.responsible.user.email} увидит его в своём списке.")
            return redirect("workspace:remark", project_id=project.pk, remark_id=remark.pk)
    return render(request, "workspace/form.html", context(request, project, "remarks", form=form, title="Новое замечание", section="замечания",
                  subtitle="Опишите дефект, выберите, кто его устраняет, и срок. Замечание к этапу на приёмке вернёт этап в работу.",
                  cancel_url="workspace:remarks"))


@login_required
def remark_detail(request, project_id, remark_id):
    project = own_project(request, project_id)
    remark = visible_remark(request, project, remark_id)
    member = active_membership(project, request.user)
    can_judge = quality_services.can_judge(remark, member) and not project.is_archived
    can_fix = (remark.responsible_id == member.pk or member.role == "foreman") and not project.is_archived
    comments = remark.comments.select_related("author").prefetch_related("photos")
    events = project.events.filter(data__remark_id=remark.pk).select_related("actor")[:30]
    return render(request, "workspace/remark_detail.html", context(
        request, project, "remarks", remark=remark, comments=comments, events=events, can_judge=can_judge, can_fix=can_fix,
        defect_photos=remark.photos.filter(comment__isnull=True)))


@login_required
def remark_edit(request, project_id, remark_id):
    project = own_project(request, project_id)
    remark = visible_remark(request, project, remark_id)
    member = active_membership(project, request.user)
    if not quality_services.can_judge(remark, member) or remark.status != "open" or project.is_archived:
        raise PermissionDenied("Изменить можно только открытое замечание — автору или заказчику.")
    author = project.members.filter(user=remark.created_by).first()
    form = RemarkEditForm(request.POST or None, remark=remark, author_role=author.role if author else member.role)
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        version = data.pop("version")
        try:
            quality_services.update_remark(remark=remark, actor=request.user, version=version, **data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Замечание сохранено.")
            return redirect("workspace:remark", project_id=project.pk, remark_id=remark.pk)
    return render(request, "workspace/form.html", context(request, project, "remarks", form=form, title="Редактирование замечания",
                  section="замечания", subtitle=f"Этап «{remark.stage.name}».", cancel_url="workspace:remarks"))


@login_required
@require_POST
def remark_action(request, project_id, remark_id):
    project = own_project(request, project_id)
    remark = visible_remark(request, project, remark_id)
    labels = {"reply": "Ответ добавлен.", "fix": "Отмечено как устранённое. Автор замечания проверит результат.",
              "close": "Устранение подтверждено, замечание закрыто.", "reopen": "Замечание возвращено на доработку.",
              "withdraw": "Замечание снято."}
    action = request.POST.get("action")
    if action not in labels:
        raise Http404
    run_service(request, lambda: quality_services.remark_action(remark=remark, actor=request.user, action=action,
                version=request.POST.get("version"), text=request.POST.get("text", ""), images=request.FILES.getlist("images")), labels[action])
    return redirect("workspace:remark", project_id=project.pk, remark_id=remark.pk)
