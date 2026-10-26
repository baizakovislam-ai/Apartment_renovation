from uuid import uuid4

from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.projects.permissions import require_manager, require_member
from apps.projects.services import atomic_operation, lock_project, record_event
from .models import Task


def visible_tasks(project, user):
    member = require_member(project, user)
    tasks = project.tasks.select_related("stage", "assignee__user", "created_by")
    return tasks.filter(assignee=member) if member.role == "executor" else tasks


def require_task_access(task, actor):
    member = require_member(task.project, actor)
    if member.role == "executor" and task.assignee_id != member.pk:
        raise PermissionDenied("Доступны только назначенные вам задачи.")
    return member


def check_version(instance, version):
    if version is None or instance.version != version:
        raise ValidationError("Данные уже изменились. Обновите страницу и повторите действие.")


def editable_stage(stage):
    if stage.status in ["pending_acceptance", "accepted", "cancelled"]:
        raise ValidationError("Этап закрыт для изменения задач и отчётов.")


def validate_task(project, data):
    if data["stage"].project_id != project.pk:
        raise ValidationError({"stage": "Выберите этап этого проекта."})
    editable_stage(data["stage"])
    assignee = data["assignee"]
    if assignee.project_id != project.pk or assignee.role != "executor":
        raise ValidationError({"assignee": "Назначьте исполнителя из команды проекта."})
    stage = data["stage"]
    due = data["due_date"]
    if (stage.start_date and due < stage.start_date) or (stage.end_date and due > stage.end_date):
        raise ValidationError({"due_date": "Срок задачи должен попадать в сроки этапа."})
    if not data["name"].strip():
        raise ValidationError({"name": "Укажите название задачи."})


@atomic_operation
def create_task(*, project, actor, request_id=None, **data):
    project = lock_project(project)
    require_manager(project, actor)
    request_id = request_id or uuid4()
    existing = Task.objects.filter(request_id=request_id).first()
    if existing:
        if existing.project_id == project.pk and existing.created_by_id == actor.pk:
            return existing
        raise ValidationError("Идентификатор операции уже использован.")
    data["stage"].refresh_from_db()
    data["assignee"].refresh_from_db()
    validate_task(project, data)
    task = Task.objects.create(project=project, created_by=actor, request_id=request_id, **data)
    record_event(project, actor, "task.created", task_id=task.pk, name=task.name)
    return task


@atomic_operation
def update_task(*, task, actor, version, **data):
    project = lock_project(task.project)
    require_manager(project, actor)
    task.refresh_from_db()
    check_version(task, version)
    editable_stage(task.stage)
    if task.status != "todo":
        raise ValidationError("Редактировать можно только задачу к выполнению.")
    merged = {key: data.get(key, getattr(task, key)) for key in ["stage", "name", "description", "assignee", "due_date"]}
    merged["stage"].refresh_from_db()
    merged["assignee"].refresh_from_db()
    validate_task(project, merged)
    if task.photo_reports.exists() and merged["stage"].pk != task.stage_id:
        raise ValidationError("Нельзя перенести задачу с фотоотчётами в другой этап.")
    for key, value in merged.items():
        setattr(task, key, value)
    task.version += 1
    task.save()
    record_event(project, actor, "task.updated", task_id=task.pk, name=task.name)
    return task


@atomic_operation
def delete_task(*, task, actor, version):
    project = lock_project(task.project)
    require_manager(project, actor)
    task.refresh_from_db()
    check_version(task, version)
    editable_stage(task.stage)
    if task.status != "todo" or task.created_by_id != actor.pk:
        raise ValidationError("Можно удалить только собственный черновик задачи.")
    task_id, name = task.pk, task.name
    try:
        task.delete()
    except ProtectedError as error:
        raise ValidationError("У задачи есть фотоотчёты. Удаление запрещено.") from error
    record_event(project, actor, "task.deleted", task_id=task_id, name=name)


@atomic_operation
def transition_task(*, task, actor, action, version, result=""):
    project = lock_project(task.project)
    task.refresh_from_db()
    member = require_task_access(task, actor)
    check_version(task, version)
    editable_stage(task.stage)
    if task.stage.status not in ["in_progress", "completed"]:
        raise ValidationError("Работы по этапу ещё не запущены заказчиком после утверждения сметы.")
    if action in ["start", "submit"]:
        if member.role != "executor" or task.assignee_id != member.pk:
            raise PermissionDenied("Выполнение доступно назначенному исполнителю.")
        if action == "start" and task.status == "todo":
            task.status = "in_progress"
        elif action == "submit" and task.status == "in_progress":
            if not result.strip() and not task.photo_reports.exists():
                raise ValidationError("Опишите результат или добавьте фотоотчёт.")
            task.status, task.result = "review", result.strip()
        else:
            raise ValidationError("Этот переход статуса недоступен.")
    elif action in ["approve", "return"]:
        require_manager(project, actor)
        if task.status != "review":
            raise ValidationError("Задача ещё не передана на проверку.")
        if action == "return" and not result.strip():
            raise ValidationError("Укажите причину возврата.")
        task.status = "done" if action == "approve" else "in_progress"
    else:
        raise ValidationError("Неизвестное действие.")
    task.version += 1
    task.save()
    record_event(project, actor, "task." + action, task_id=task.pk, name=task.name, reason=result.strip())
    return task


def project_progress(project):
    stages = project.stages.exclude(status="cancelled").aggregate(total=Count("id"), accepted=Count("id", filter=Q(status="accepted")))
    tasks = project.tasks.exclude(stage__status="cancelled").aggregate(total=Count("id"), done=Count("id", filter=Q(status="done")), overdue=Count("id", filter=Q(due_date__lt=timezone.localdate()) & ~Q(status="done")))
    return {"percent": round(100 * stages["accepted"] / stages["total"]) if stages["total"] else 0,
            "stages": stages["total"], "accepted": stages["accepted"], "tasks": tasks["total"], "done": tasks["done"], "overdue": tasks["overdue"]}
