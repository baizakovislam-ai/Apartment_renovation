"""Tasks, deadlines, photo reports and progress (ТЗ FR05, FR06, FR08)."""
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.projects.models import ProjectMember
from apps.projects.permissions import require_manager, require_member
from apps.projects.services import atomic_operation, lock_project, record_event
from .images import process_photo
from .models import Photo, PhotoReport, Stage, Task

CLOSED_STAGE_STATUSES = {Stage.Status.PENDING_ACCEPTANCE, Stage.Status.ACCEPTED, Stage.Status.CANCELLED}
WORKING_STAGE_STATUSES = {Stage.Status.IN_PROGRESS, Stage.Status.COMPLETED}
TASK_FIELDS = ["stage", "name", "description", "assignee", "due_date"]
STALE_MESSAGE = "Задачу уже изменил другой пользователь. Обновите страницу и повторите действие."


def is_manager(member):
    return member.role in (ProjectMember.Role.CUSTOMER, ProjectMember.Role.FOREMAN)


def visible_tasks(project, user):
    """Managers see every task; an executor sees only the tasks assigned to them."""
    member = require_member(project, user)
    tasks = project.tasks.select_related("stage", "assignee__user")
    return tasks if is_manager(member) else tasks.filter(assignee=member)


def require_task_access(task, user):
    member = require_member(task.project, user)
    if not is_manager(member) and task.assignee_id != member.pk:
        raise PermissionDenied("Эта задача назначена другому исполнителю.")
    return member


def check_version(task, version):
    if version is None or int(version) != task.version:
        raise ValidationError({"version": STALE_MESSAGE})


def lock_task(task):
    return Task.objects.select_for_update().select_related("stage", "project").get(pk=task.pk)


def validate_task(project, data, instance=None):
    unknown = set(data) - set(TASK_FIELDS)
    if unknown:
        raise ValidationError(f"Неизвестные поля задачи: {', '.join(sorted(unknown))}.")
    stage = data.get("stage", instance.stage if instance else None)
    if stage is None or stage.project_id != project.pk:
        raise ValidationError({"stage": "Этап должен относиться к этому проекту."})
    if stage.status in CLOSED_STAGE_STATUSES and (instance is None or "stage" in data):
        raise ValidationError({"stage": "Этап на приёмке, принят или отменён — задачи в нём не добавляются."})
    assignee = data.get("assignee", instance.assignee if instance else None)
    if assignee is None or assignee.project_id != project.pk or assignee.role != ProjectMember.Role.EXECUTOR:
        raise ValidationError({"assignee": "Исполнитель должен быть участником проекта с ролью «Исполнитель»."})
    due_date = data.get("due_date", instance.due_date if instance else None)
    if due_date is None or not stage.start_date <= due_date <= stage.end_date:
        raise ValidationError({"due_date": f"Срок должен попадать в сроки этапа: {stage.start_date:%d.%m.%Y} — {stage.end_date:%d.%m.%Y}."})
    if "name" in data and not data["name"].strip():
        raise ValidationError({"name": "Укажите название задачи."})


@atomic_operation
def create_task(*, project, actor, request_id=None, **data):
    project = lock_project(project)
    require_manager(project, actor)
    if request_id:
        existing = Task.objects.filter(request_id=request_id).first()
        if existing:
            # A repeated form submission returns the task created by the first click.
            if existing.project_id != project.pk or existing.created_by_id != actor.pk:
                raise ValidationError("Повторный идентификатор запроса.")
            return existing
    validate_task(project, data)
    extra = {"request_id": request_id} if request_id else {}
    task = Task.objects.create(project=project, created_by=actor, **extra, **data)
    record_event(project, actor, "task.created", task_id=task.pk, name=task.name, assignee_id=task.assignee.user_id, due_date=str(task.due_date))
    return task


@atomic_operation
def update_task(*, task, actor, version, **data):
    project = lock_project(task.project)
    require_manager(project, actor)
    task = lock_task(task)
    check_version(task, version)
    if task.status == Task.Status.DONE:
        raise ValidationError("Выполненную задачу изменить нельзя.")
    if task.stage.status in CLOSED_STAGE_STATUSES:
        raise ValidationError("Этап задачи на приёмке или закрыт — изменения запрещены.")
    validate_task(project, data, task)
    changed = {key: value for key, value in data.items() if getattr(task, key) != value}
    if "assignee" in changed and task.status != Task.Status.TODO:
        raise ValidationError({"assignee": "Исполнителя можно сменить только до начала выполнения."})
    if not changed:
        return task
    before = {key: str(getattr(task, key)) for key in changed}
    for key, value in changed.items():
        setattr(task, key, value)
    task.version += 1
    task.save()
    record_event(project, actor, "task.updated", task_id=task.pk, name=task.name, before=before, after={key: str(value) for key, value in changed.items()})
    return task


@atomic_operation
def delete_task(*, task, actor, version):
    project = lock_project(task.project)
    require_manager(project, actor)
    task = lock_task(task)
    check_version(task, version)
    if task.created_by_id != actor.pk or task.status != Task.Status.TODO:
        raise ValidationError("Удалить можно только собственную задачу, к которой ещё не приступили.")
    if task.reports.exists() or task.remarks.exists():
        raise ValidationError("У задачи есть фотоотчёты или замечания. Удаление запрещено.")
    task_id, name = task.pk, task.name
    task.delete()
    record_event(project, actor, "task.deleted", task_id=task_id, name=name)


@atomic_operation
def task_action(*, task, actor, action, version, text=""):
    """Status flow: К выполнению → В работе → На проверке → Выполнена (ТЗ, прил. Б)."""
    project = lock_project(task.project)
    member = require_task_access(task, actor)
    task = lock_task(task)
    check_version(task, version)
    text = (text or "").strip()
    is_assignee = task.assignee_id == member.pk
    if task.stage.status in {Stage.Status.ACCEPTED, Stage.Status.CANCELLED}:
        raise ValidationError("Этап задачи уже принят или отменён.")
    if action == "start":
        if not is_assignee:
            raise PermissionDenied("Начать выполнение может только назначенный исполнитель.")
        if task.status != Task.Status.TODO:
            raise ValidationError("Задача уже в работе.")
        if task.stage.status not in WORKING_STAGE_STATUSES:
            raise ValidationError("Этап ещё не запущен. Дождитесь начала работ по этапу.")
        task.status = Task.Status.IN_PROGRESS
    elif action == "submit":
        if not is_assignee:
            raise PermissionDenied("Передать на проверку может только назначенный исполнитель.")
        if task.status != Task.Status.IN_PROGRESS:
            raise ValidationError("На проверку передаётся только задача в работе.")
        if not text and not task.result and not task.reports.exists():
            raise ValidationError({"text": "Опишите результат или приложите фотоотчёт."})
        if text:
            task.result = text
        task.status = Task.Status.REVIEW
    elif action in ("approve", "return"):
        if not is_manager(member):
            raise PermissionDenied("Проверку выполняют заказчик или прораб.")
        if task.status != Task.Status.REVIEW:
            raise ValidationError("Задача не находится на проверке.")
        if action == "approve":
            task.status = Task.Status.DONE
            task.completed_at = timezone.now()
            task.approved_by = actor
            task.return_reason = ""
        else:
            if not text:
                raise ValidationError({"text": "Укажите, что нужно доработать."})
            task.status = Task.Status.IN_PROGRESS
            task.return_reason = text
    else:
        raise ValidationError({"action": "Неизвестное действие."})
    task.version += 1
    task.save()
    record_event(project, actor, f"task.{action}", task_id=task.pk, name=task.name, **({"text": text} if text else {}))
    return task


@atomic_operation
def add_photo_report(*, task, actor, description, images, request_id=None):
    project = lock_project(task.project)
    require_task_access(task, actor)
    task = lock_task(task)
    if request_id:
        existing = PhotoReport.objects.filter(request_id=request_id).first()
        if existing:
            if existing.task_id != task.pk or existing.author_id != actor.pk:
                raise ValidationError("Повторный идентификатор запроса.")
            return existing
    if task.stage.status == Stage.Status.CANCELLED:
        raise ValidationError("Этап отменён.")
    description = (description or "").strip()
    if not description:
        raise ValidationError({"description": "Добавьте описание к фотоотчёту."})
    images = list(images or [])
    if not 1 <= len(images) <= settings.PHOTO_MAX_FILES:
        raise ValidationError({"images": f"Приложите от 1 до {settings.PHOTO_MAX_FILES} фотографий."})
    processed = [process_photo(upload) for upload in images]
    extra = {"request_id": request_id} if request_id else {}
    report = PhotoReport.objects.create(project=project, task=task, author=actor, description=description, **extra)
    for content, content_type, width, height, size in processed:
        Photo.objects.create(report=report, file=content, content_type=content_type, width=width, height=height, size=size)
    record_event(project, actor, "photo_report.created", task_id=task.pk, name=task.name, photos=len(processed))
    return report


def stage_progress(stage):
    """An accepted stage counts 100%; before acceptance up to 90% from approved tasks."""
    if stage.status == Stage.Status.ACCEPTED:
        return 100
    total, done = getattr(stage, "task_total", None), getattr(stage, "task_done", None)
    if total is None:
        total, done = stage.tasks.count(), stage.tasks.filter(status=Task.Status.DONE).count()
    return round(90 * done / total) if total else 0


def project_progress(project):
    stages = list(project.stages.exclude(status=Stage.Status.CANCELLED).annotate(
        task_total=Count("tasks"), task_done=Count("tasks", filter=Q(tasks__status=Task.Status.DONE))))
    rows = [(stage, stage_progress(stage)) for stage in stages]
    today = timezone.localdate()
    tasks = project.tasks.aggregate(
        total=Count("id"), done=Count("id", filter=Q(status=Task.Status.DONE)),
        review=Count("id", filter=Q(status=Task.Status.REVIEW)),
        overdue=Count("id", filter=Q(due_date__lt=today) & ~Q(status=Task.Status.DONE)))
    return {
        "percent": round(sum(value for _, value in rows) / len(rows)) if rows else 0,
        "stages_total": len(rows),
        "stages_accepted": sum(stage.status == Stage.Status.ACCEPTED for stage in stages),
        "stages_pending": sum(stage.status == Stage.Status.PENDING_ACCEPTANCE for stage in stages),
        "tasks_total": tasks["total"], "tasks_done": tasks["done"], "tasks_review": tasks["review"], "tasks_overdue": tasks["overdue"],
        "stages": rows,
    }
