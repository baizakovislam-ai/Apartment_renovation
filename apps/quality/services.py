"""Stage acceptance and remarks (ТЗ FR07, scenario 4.2)."""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.projects.models import ProjectMember
from apps.projects.permissions import require_customer, require_manager, require_member
from apps.projects.services import atomic_operation, lock_project, record_event
from apps.repairs.images import process_photo
from apps.repairs.models import Stage, Task
from .models import Remark, RemarkComment, RemarkPhoto, StageDecision

UNRESOLVED = [Remark.Status.OPEN, Remark.Status.FIXED]
REMARK_STALE = "Замечание уже изменил другой пользователь. Обновите страницу."


def lock_stage(stage):
    return Stage.objects.select_for_update().get(pk=stage.pk)


def acceptance_blockers(stage):
    """Reasons a stage cannot go to (or pass) acceptance yet."""
    reasons = []
    if not stage.tasks.exists():
        reasons.append("В этапе нет ни одной задачи — нечего принимать")
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

# ---------- Remarks ----------
#
# Who does what:
#   create  — customer (to the foreman or an executor) or foreman (to an executor);
#   edit / withdraw — the author or the customer, while the remark is open;
#   reply   — anyone who can see the remark;
#   fix     — the responsible person or the foreman;
#   close / reopen (confirm or reject the fix) — the author or the customer (ТЗ: заказчик подтверждает устранение).

REMARK_STAGE_STATUSES = (Stage.Status.IN_PROGRESS, Stage.Status.COMPLETED, Stage.Status.PENDING_ACCEPTANCE)


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


def can_judge(remark, member):
    """The author or the customer confirms, rejects, edits or withdraws a remark."""
    return member.role == ProjectMember.Role.CUSTOMER or remark.created_by_id == member.user_id


def check_remark_version(remark, version):
    if version is None or str(version) == "" or int(version) != remark.version:
        raise ValidationError({"version": REMARK_STALE})


def save_photos(remark, actor, images, comment=None):
    images = [image for image in (images or []) if image]
    if len(images) > settings.PHOTO_MAX_FILES:
        raise ValidationError({"images": f"Можно приложить не больше {settings.PHOTO_MAX_FILES} фотографий."})
    processed = [process_photo(image) for image in images]
    for content, content_type, width, height, size in processed:
        RemarkPhoto.objects.create(remark=remark, comment=comment, file=content, content_type=content_type,
                                   width=width, height=height, size=size, uploaded_by=actor)
    return len(processed)


def validate_responsible(project, author, responsible):
    if responsible is None or responsible.project_id != project.pk or not responsible.is_active:
        raise ValidationError({"responsible": "Ответственный должен быть действующим участником этого проекта."})
    if responsible.role == ProjectMember.Role.CUSTOMER:
        raise ValidationError({"responsible": "Ответственный — прораб или исполнитель."})
    if author.role == ProjectMember.Role.FOREMAN and responsible.role != ProjectMember.Role.EXECUTOR:
        raise ValidationError({"responsible": "Прораб оставляет замечания исполнителям."})


@atomic_operation
def create_remark(*, project, actor, stage, description, responsible, due_date, task=None, request_id=None, images=None):
    project = lock_project(project)
    author = require_manager(project, actor)
    if request_id:
        existing = Remark.objects.filter(request_id=request_id).first()
        if existing:
            if existing.project_id != project.pk or existing.created_by_id != actor.pk:
                raise ValidationError("Повторный идентификатор запроса.")
            return existing
    if stage is None or stage.project_id != project.pk:
        raise ValidationError({"stage": "Этап должен относиться к этому проекту."})
    stage = lock_stage(stage)
    if stage.status not in REMARK_STAGE_STATUSES:
        raise ValidationError({"stage": "Замечания оставляют к этапу, по которому идут работы или приёмка. Этот этап ещё не начат или уже закрыт."})
    if task is not None and task.stage_id != stage.pk:
        raise ValidationError({"task": "Задача должна относиться к выбранному этапу."})
    validate_responsible(project, author, responsible)
    if due_date is None or due_date < timezone.localdate():
        raise ValidationError({"due_date": "Срок устранения не может быть в прошлом."})
    description = (description or "").strip()
    if not description:
        raise ValidationError({"description": "Опишите замечание."})
    extra = {"request_id": request_id} if request_id else {}
    remark = Remark.objects.create(project=project, stage=stage, task=task, description=description, responsible=responsible,
                                   due_date=due_date, created_by=actor, **extra)
    photos = save_photos(remark, actor, images)
    record_event(project, actor, "remark.created", remark_id=remark.pk, stage_id=stage.pk, name=stage.name, photos=photos)
    if stage.status == Stage.Status.PENDING_ACCEPTANCE:
        # A remark during acceptance sends the stage back to work automatically.
        stage.status = Stage.Status.IN_PROGRESS
        stage.save(update_fields=["status", "updated_at"])
        decide(stage, actor, StageDecision.Action.RETURN, f"Замечание: {description}")
    return remark


@atomic_operation
def update_remark(*, remark, actor, version, **data):
    project = lock_project(remark.project)
    member = require_remark_access(remark, actor)
    remark = Remark.objects.select_for_update().get(pk=remark.pk)
    check_remark_version(remark, version)
    if not can_judge(remark, member):
        raise PermissionDenied("Изменить замечание может его автор или заказчик.")
    if remark.status != Remark.Status.OPEN:
        raise ValidationError("Изменять можно только открытое замечание.")
    unknown = set(data) - {"description", "responsible", "due_date"}
    if unknown:
        raise ValidationError(f"Неизвестные поля: {', '.join(sorted(unknown))}.")
    author = ProjectMember.objects.filter(project=project, user=remark.created_by).first() or member
    if "responsible" in data and data["responsible"] != remark.responsible:
        validate_responsible(project, author, data["responsible"])
    if "due_date" in data and data["due_date"] != remark.due_date and data["due_date"] < timezone.localdate():
        raise ValidationError({"due_date": "Срок устранения не может быть в прошлом."})
    if "description" in data:
        data["description"] = (data["description"] or "").strip()
        if not data["description"]:
            raise ValidationError({"description": "Опишите замечание."})
    changed = {key: value for key, value in data.items() if getattr(remark, key) != value}
    if not changed:
        return remark
    for key, value in changed.items():
        setattr(remark, key, value)
    remark.version += 1
    remark.save()
    record_event(project, actor, "remark.updated", remark_id=remark.pk, fields=sorted(changed))
    return remark


@atomic_operation
def remark_action(*, remark, actor, action, version=None, text="", images=None):
    project = lock_project(remark.project)
    member = require_remark_access(remark, actor)
    remark = Remark.objects.select_for_update().get(pk=remark.pk)
    text = (text or "").strip()
    images = [image for image in (images or []) if image]
    if action == "reply":
        if not text and not images:
            raise ValidationError({"text": "Напишите ответ или приложите фото."})
        if remark.status == Remark.Status.CLOSED:
            raise ValidationError("Замечание закрыто.")
        comment = RemarkComment.objects.create(remark=remark, author=actor, text=text, action="reply")
        save_photos(remark, actor, images, comment)
        record_event(project, actor, "remark.reply", remark_id=remark.pk)
        return remark
    check_remark_version(remark, version)
    if action == "fix":
        if remark.responsible_id != member.pk and member.role != ProjectMember.Role.FOREMAN:
            raise PermissionDenied("Сообщить об устранении может ответственный или прораб.")
        if remark.status != Remark.Status.OPEN:
            raise ValidationError("Замечание не открыто.")
        remark.status = Remark.Status.FIXED
    elif action == "close":
        if not can_judge(remark, member):
            raise PermissionDenied("Подтвердить устранение может автор замечания или заказчик.")
        if remark.status != Remark.Status.FIXED:
            raise ValidationError("Подтвердить можно только замечание, отмеченное как устранённое.")
        remark.status = Remark.Status.CLOSED
        remark.closed_by, remark.closed_at = actor, timezone.now()
    elif action == "reopen":
        if not can_judge(remark, member):
            raise PermissionDenied("Вернуть замечание может автор или заказчик.")
        if remark.status != Remark.Status.FIXED:
            raise ValidationError("Вернуть можно только замечание, отмеченное как устранённое.")
        if not text:
            raise ValidationError({"text": "Напишите, что не устранено."})
        remark.status = Remark.Status.OPEN
    elif action == "withdraw":
        if not can_judge(remark, member):
            raise PermissionDenied("Снять замечание может его автор или заказчик.")
        if remark.status == Remark.Status.CLOSED:
            raise ValidationError("Замечание уже закрыто.")
        if not text:
            raise ValidationError({"text": "Укажите, почему замечание снимается."})
        remark.status = Remark.Status.CLOSED
        remark.withdrawn = True
        remark.closed_by, remark.closed_at = actor, timezone.now()
    else:
        raise ValidationError({"action": "Неизвестное действие."})
    remark.version += 1
    remark.save()
    if text or images:
        comment = RemarkComment.objects.create(remark=remark, author=actor, text=text, action=action)
        save_photos(remark, actor, images, comment)
    record_event(project, actor, f"remark.{action}", remark_id=remark.pk)
    return remark
