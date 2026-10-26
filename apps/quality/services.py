"""Stage acceptance and remarks (ТЗ FR07, scenario 4.2)."""
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.projects.models import ProjectMember
from apps.projects.permissions import require_customer, require_member
from apps.projects.services import atomic_operation, lock_project, record_event
from apps.repairs.models import Stage, Task
from .models import Remark, RemarkComment, StageDecision

UNRESOLVED = [Remark.Status.OPEN, Remark.Status.FIXED]
REMARK_STALE = "Замечание уже изменил другой пользователь. Обновите страницу."


def lock_stage(stage):
    return Stage.objects.select_for_update().get(pk=stage.pk)


def acceptance_blockers(stage):
    """Reasons a stage cannot go to (or pass) acceptance yet."""
    reasons = []
    unfinished = stage.tasks.exclude(status=Task.Status.DONE).count()
    if unfinished:
        reasons.append(f"Не выполнено задач: {unfinished}")
    unresolved = stage.remarks.filter(status__in=UNRESOLVED).count()
    if unresolved:
        reasons.append(f"Открытых замечаний: {unresolved}")
    return reasons


def decide(stage, actor, action, reason=""):
    StageDecision.objects.create(project=stage.project, stage=stage, actor=actor, action=action, reason=reason)
    record_event(stage.project, actor, f"stage.{action}", stage_id=stage.pk, name=stage.name, **({"reason": reason} if reason else {}))


@atomic_operation
def submit_stage(*, stage, actor):
    """Only the foreman hands a stage over (ТЗ, прил. А)."""
    project = lock_project(stage.project)
    require_member(project, actor, [ProjectMember.Role.FOREMAN])
    stage = lock_stage(stage)
    if stage.status not in (Stage.Status.IN_PROGRESS, Stage.Status.COMPLETED):
        raise ValidationError("На приёмку передаётся этап в работе или с выполненными работами.")
    blockers = acceptance_blockers(stage)
    if blockers:
        raise ValidationError("Этап нельзя передать на приёмку: " + "; ".join(blockers) + ".")
    stage.status = Stage.Status.PENDING_ACCEPTANCE
    stage.save(update_fields=["status", "updated_at"])
    decide(stage, actor, StageDecision.Action.SUBMIT)
    return stage


@atomic_operation
def accept_stage(*, stage, actor):
    project = lock_project(stage.project)
    require_customer(project, actor)
    stage = lock_stage(stage)
    if stage.status != Stage.Status.PENDING_ACCEPTANCE:
        raise ValidationError("Этап не находится на приёмке.")
    blockers = acceptance_blockers(stage)
    if blockers:
        raise ValidationError("Приёмка заблокирована: " + "; ".join(blockers) + ".")
    stage.status = Stage.Status.ACCEPTED
    stage.save(update_fields=["status", "updated_at"])
    decide(stage, actor, StageDecision.Action.ACCEPT)
    return stage


@atomic_operation
def return_stage(*, stage, actor, reason):
    project = lock_project(stage.project)
    require_customer(project, actor)
    stage = lock_stage(stage)
    reason = (reason or "").strip()
    if stage.status != Stage.Status.PENDING_ACCEPTANCE:
        raise ValidationError("Этап не находится на приёмке.")
    if not reason:
        raise ValidationError({"reason": "Укажите причину возврата."})
    stage.status = Stage.Status.IN_PROGRESS
    stage.save(update_fields=["status", "updated_at"])
    decide(stage, actor, StageDecision.Action.RETURN, reason)
    return stage


def visible_remarks(project, user):
    member = require_member(project, user)
    remarks = project.remarks.select_related("stage", "task", "responsible__user", "created_by")
    if member.role == ProjectMember.Role.EXECUTOR:
        remarks = remarks.filter(responsible=member)
    return remarks


def require_remark_access(remark, user):
    member = require_member(remark.project, user)
    if member.role == ProjectMember.Role.EXECUTOR and remark.responsible_id != member.pk:
        raise PermissionDenied("Замечание назначено другому участнику.")
    return member


@atomic_operation
def create_remark(*, project, actor, stage, description, responsible, due_date, task=None, request_id=None):
    project = lock_project(project)
    require_customer(project, actor)
    if request_id:
        existing = Remark.objects.filter(request_id=request_id).first()
        if existing:
            if existing.project_id != project.pk or existing.created_by_id != actor.pk:
                raise ValidationError("Повторный идентификатор запроса.")
            return existing
    if stage.project_id != project.pk:
        raise ValidationError({"stage": "Этап должен относиться к этому проекту."})
    stage = lock_stage(stage)
    if stage.status not in (Stage.Status.IN_PROGRESS, Stage.Status.COMPLETED, Stage.Status.PENDING_ACCEPTANCE):
        raise ValidationError({"stage": "Замечания оставляют к этапу в работе или на приёмке."})
    if task is not None and task.stage_id != stage.pk:
        raise ValidationError({"task": "Задача должна относиться к выбранному этапу."})
    if responsible.project_id != project.pk or responsible.role == ProjectMember.Role.CUSTOMER:
        raise ValidationError({"responsible": "Ответственный — прораб или исполнитель этого проекта."})
    if due_date < timezone.localdate():
        raise ValidationError({"due_date": "Срок устранения не может быть в прошлом."})
    description = (description or "").strip()
    if not description:
        raise ValidationError({"description": "Опишите замечание."})
    extra = {"request_id": request_id} if request_id else {}
    remark = Remark.objects.create(project=project, stage=stage, task=task, description=description, responsible=responsible,
                                   due_date=due_date, created_by=actor, **extra)
    record_event(project, actor, "remark.created", remark_id=remark.pk, stage_id=stage.pk, name=stage.name)
    if stage.status == Stage.Status.PENDING_ACCEPTANCE:
        # A remark during acceptance sends the stage back to work automatically.
        stage.status = Stage.Status.IN_PROGRESS
        stage.save(update_fields=["status", "updated_at"])
        decide(stage, actor, StageDecision.Action.RETURN, f"Замечание: {description}")
    return remark


@atomic_operation
def remark_action(*, remark, actor, action, version=None, text=""):
    project = lock_project(remark.project)
    member = require_remark_access(remark, actor)
    remark = Remark.objects.select_for_update().get(pk=remark.pk)
    text = (text or "").strip()
    is_customer = member.role == ProjectMember.Role.CUSTOMER
    if action == "reply":
        if not text:
            raise ValidationError({"text": "Введите текст ответа."})
        if remark.status == Remark.Status.CLOSED:
            raise ValidationError("Замечание закрыто.")
        RemarkComment.objects.create(remark=remark, author=actor, text=text, action="reply")
        record_event(project, actor, "remark.reply", remark_id=remark.pk)
        return remark
    if version is None or int(version) != remark.version:
        raise ValidationError({"version": REMARK_STALE})
    if action == "fix":
        if remark.responsible_id != member.pk and member.role != ProjectMember.Role.FOREMAN:
            raise PermissionDenied("Сообщить об устранении может ответственный или прораб.")
        if remark.status != Remark.Status.OPEN:
            raise ValidationError("Замечание не открыто.")
        remark.status = Remark.Status.FIXED
    elif action == "close":
        if not is_customer:
            raise PermissionDenied("Закрывает замечание только заказчик.")
        if remark.status == Remark.Status.CLOSED:
            raise ValidationError("Замечание уже закрыто.")
        remark.status = Remark.Status.CLOSED
        remark.closed_by = actor
        remark.closed_at = timezone.now()
    elif action == "reopen":
        if not is_customer:
            raise PermissionDenied("Вернуть замечание может только заказчик.")
        if remark.status != Remark.Status.FIXED:
            raise ValidationError("Вернуть можно только замечание, отмеченное как устранённое.")
        if not text:
            raise ValidationError({"text": "Укажите, что не устранено."})
        remark.status = Remark.Status.OPEN
    else:
        raise ValidationError({"action": "Неизвестное действие."})
    remark.version += 1
    remark.save()
    if text:
        RemarkComment.objects.create(remark=remark, author=actor, text=text, action=action)
    record_event(project, actor, f"remark.{action}", remark_id=remark.pk)
    return remark
