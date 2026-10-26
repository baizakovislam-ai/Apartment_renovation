from django.db.models.deletion import ProtectedError
from django.db.models import Max
from rest_framework.exceptions import ValidationError

from apps.projects.models import ProjectMember
from apps.projects.services import atomic_operation, lock_project, record_event
from .models import Stage
from .permissions import require_manager


def validate_stage(project, data, instance=None):
    start = data.get("start_date", instance.start_date if instance else None)
    end = data.get("end_date", instance.end_date if instance else None)
    if start is not None and end is not None and end < start:
        raise ValidationError({"end_date": "Окончание не может быть раньше начала."})
    responsible = data.get("responsible", instance.responsible if instance else None)
    if responsible is not None and not ProjectMember.objects.filter(pk=responsible.pk, project=project).exists():
        raise ValidationError({"responsible": "Ответственный должен участвовать в этом проекте."})
    target = data.get("status", instance.status if instance else "planned")
    if target != "planned" and (not start or not end or not responsible):
        raise ValidationError("Перед началом работ укажите обе даты и ответственного из команды проекта.")
    if instance is None:
        if target != "planned":
            raise ValidationError({"status": "Новый этап должен быть запланированным."})
    elif instance.status in ["pending_acceptance", "accepted", "cancelled"]:
        raise ValidationError({"status": "Этап на приёмке или закрыт для редактирования."})
    elif target != instance.status:
        # FR04 edits planning data. Lifecycle actions need the approved estimate,
        # project launch by the customer, then handover by the foreman (UC-8/12/13).
        raise ValidationError({"status": "Статус нельзя менять через редактирование этапа. Запуск работ требует принятого приглашения прораба и утверждённой сметы; передачу на приёмку выполняет прораб, приёмку — заказчик."})
    if instance:
        if (start and instance.tasks.filter(due_date__lt=start).exists()) or (end and instance.tasks.filter(due_date__gt=end).exists()):
            raise ValidationError("Новые сроки этапа не включают сроки его задач. Сначала скорректируйте задачи.")


@atomic_operation
def create_stage(*, project, actor, **data):
    project = lock_project(project)
    require_manager(project, actor)
    validate_stage(project, data)
    if "position" not in data:
        data["position"] = (project.stages.aggregate(last=Max("position"))["last"] or 0) + 1
    stage = Stage.objects.create(project=project, created_by=actor, **data)
    record_event(project, actor, "stage.created", stage_id=stage.pk, name=stage.name)
    return stage


@atomic_operation
def move_stage(*, stage, actor, direction):
    project = lock_project(stage.project)
    require_manager(project, actor)
    if direction not in ["up", "down"]:
        raise ValidationError("Неизвестное направление перемещения.")
    stages = list(project.stages.all())
    index = next(i for i, item in enumerate(stages) if item.pk == stage.pk)
    target = index + (-1 if direction == "up" else 1)
    if target < 0 or target >= len(stages):
        return
    stages[index], stages[target] = stages[target], stages[index]
    for position, item in enumerate(stages, 1):
        item.position = position
    Stage.objects.bulk_update(stages, ["position"])
    record_event(project, actor, "stage.reordered", stage_id=stage.pk, direction=direction)


@atomic_operation
def update_stage(*, stage, actor, **data):
    project = lock_project(stage.project)
    require_manager(project, actor)
    stage.refresh_from_db()
    validate_stage(project, data, stage)
    before = {key: str(getattr(stage, key)) for key in data}
    for key, value in data.items():
        setattr(stage, key, value)
    stage.save()
    record_event(project, actor, "stage.updated", stage_id=stage.pk, before=before, after={key: str(value) for key, value in data.items()})
    return stage


@atomic_operation
def delete_stage(*, stage, actor):
    project = lock_project(stage.project)
    require_manager(project, actor)
    stage.refresh_from_db()
    if stage.created_by_id != actor.pk:
        raise ValidationError("Можно удалить только собственный черновик этапа.")
    if stage.status != "planned":
        raise ValidationError("Можно удалить только запланированный этап без зависимых записей.")
    # Future modules must use PROTECT for stage references; do not cascade financial records.
    for relation in Stage._meta.related_objects:
        if relation.one_to_many and getattr(stage, relation.get_accessor_name()).exists():
            raise ValidationError("Этап имеет связанные записи. Удаление запрещено.")
    stage_id, name = stage.pk, stage.name
    try:
        stage.delete()
    except ProtectedError as error:
        raise ValidationError("Этап имеет связанные записи. Удаление запрещено.") from error
    record_event(project, actor, "stage.deleted", stage_id=stage_id, name=name)
