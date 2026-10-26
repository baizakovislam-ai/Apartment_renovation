from uuid import uuid4

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.projects.permissions import require_customer, require_member
from apps.projects.services import atomic_operation, lock_project, record_event
from apps.repairs.task_services import check_version
from .models import Remark, RemarkReply, StageDecision


def visible_remarks(project, actor):
    member = require_member(project, actor)
    qs = project.remarks.select_related("stage", "responsible__user", "created_by", "closed_by").prefetch_related("replies__author")
    return qs.filter(responsible=member) if member.role == "executor" else qs


@atomic_operation
def create_remark(*, project, actor, stage, description, responsible, due_date, request_id=None):
    project = lock_project(project)
    require_customer(project, actor)
    request_id = request_id or uuid4()
    existing = Remark.objects.filter(request_id=request_id).first()
    if existing:
        if existing.project_id == project.pk and existing.created_by_id == actor.pk:
            return existing
        raise ValidationError("Идентификатор операции уже использован.")
    stage.refresh_from_db()
    responsible.refresh_from_db()
    if stage.project_id != project.pk or responsible.project_id != project.pk or responsible.role not in ["foreman", "executor"]:
        raise ValidationError("Этап и ответственный должны относиться к этому проекту.")
    if stage.status not in ["in_progress", "completed", "pending_acceptance"]:
        raise ValidationError("Замечания доступны по выполняемым работам и на приёмке.")
    if not description.strip():
        raise ValidationError("Опишите замечание.")
    remark = Remark.objects.create(project=project, stage=stage, description=description.strip(), responsible=responsible, due_date=due_date, created_by=actor, request_id=request_id)
    if stage.status in ["completed", "pending_acceptance"]:
        stage.status = "in_progress"
    stage.acceptance_version += 1
    stage.save(update_fields=["status", "acceptance_version", "updated_at"])
    record_event(project, actor, "remark.created", stage_id=stage.pk, remark_id=remark.pk)
    return remark


@atomic_operation
def remark_action(*, remark, actor, action, version, text=""):
    project = lock_project(remark.project)
    remark.refresh_from_db()
    member = require_member(project, actor)
    if member.role == "executor" and remark.responsible_id != member.pk:
        raise PermissionDenied("Это замечание назначено другому участнику.")
    check_version(remark, version)
    if action == "fix":
        if member.role not in ["foreman", "executor"]:
            raise PermissionDenied("Устранение отмечает исполнитель или прораб.")
        if remark.status != "open" or not text.strip():
            raise ValidationError("Опишите устранение открытого замечания.")
        remark.status = "fixed"
    elif action in ["close", "reopen"]:
        require_customer(project, actor)
        if remark.status != "fixed":
            raise ValidationError("Сначала дождитесь сообщения об устранении.")
        if action == "reopen" and not text.strip():
            raise ValidationError("Укажите причину повторной доработки.")
        remark.status = "closed" if action == "close" else "open"
        if action == "close":
            remark.closed_by, remark.closed_at = actor, timezone.now()
    elif action == "reply":
        if remark.status == "closed" or not text.strip():
            raise ValidationError("Введите ответ к незакрытому замечанию.")
    else:
        raise ValidationError("Неизвестное действие.")
    if text.strip():
        RemarkReply.objects.create(remark=remark, author=actor, text=text.strip())
    remark.version += 1
    remark.save()
    record_event(project, actor, "remark." + action, remark_id=remark.pk, stage_id=remark.stage_id)
    return remark


@atomic_operation
def stage_action(*, stage, actor, action, version, reason=""):
    project = lock_project(stage.project)
    stage.refresh_from_db()
    member = require_member(project, actor)
    if version != stage.acceptance_version:
        raise ValidationError("Состояние приёмки изменилось. Обновите страницу.")
    if action == "submit":
        if member.role != "foreman":
            raise PermissionDenied("Передать этап на приёмку может только прораб.")
        if stage.status not in ["in_progress", "completed"]:
            raise ValidationError("Передать можно только выполняемый этап.")
        if not stage.tasks.exists() or stage.tasks.exclude(status="done").exists():
            raise ValidationError("Для приёмки завершите и проверьте все задачи этапа.")
        if stage.remarks.exclude(status="closed").exists():
            raise ValidationError("Заказчик должен закрыть все замечания.")
        stage.status = "pending_acceptance"
    elif action in ["accept", "return"]:
        require_customer(project, actor)
        if stage.status != "pending_acceptance":
            raise ValidationError("Этап ещё не передан прорабом на приёмку.")
        if action == "accept":
            if stage.remarks.exclude(status="closed").exists() or stage.tasks.exclude(status="done").exists() or not stage.tasks.exists():
                raise ValidationError("Приёмка запрещена: есть незавершённые задачи или незакрытые замечания.")
            stage.status = "accepted"
        else:
            if not reason.strip():
                raise ValidationError("Укажите причину возврата на доработку.")
            stage.status = "in_progress"
    else:
        raise ValidationError("Неизвестное действие приёмки.")
    stage.acceptance_version += 1
    stage.save(update_fields=["status", "acceptance_version", "updated_at"])
    StageDecision.objects.create(project=project, stage=stage, actor=actor, action=action, reason=reason.strip())
    record_event(project, actor, "acceptance." + action, stage_id=stage.pk, name=stage.name, reason=reason.strip())
    return stage
