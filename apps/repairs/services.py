from django.db.models.deletion import ProtectedError
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
    if instance is not None and instance.tasks.exclude(due_date__range=(start, end)).exists():
        raise ValidationError({"end_date": "Сроки задач этапа выходят за новые даты. Сначала измените сроки задач."})
    responsible = data.get("responsible", instance.responsible if instance else None)
    responsible_changed = instance is None or ("responsible" in data and data["responsible"] != instance.responsible)
    if responsible is None or responsible.project_id != project.pk or (responsible_changed and not responsible.is_active):
        raise ValidationError({"responsible": "Ответственный должен участвовать в этом проекте."})
    target = data.get("status", instance.status if instance else "planned")
    transitions = {"planned": {"planned", "in_progress"}, "in_progress": {"in_progress", "completed"}, "completed": {"completed", "in_progress"}}
    if instance is None:
        if target != "planned":
            raise ValidationError({"status": "Новый этап должен быть запланированным."})
    elif target not in transitions.get(instance.status, set()):
        raise ValidationError({"status": "Недопустимый переход. Приёмка и отмена выполняются отдельным модулем."})


@atomic_operation
def create_stage(*, project, actor, **data):
    project = lock_project(project)
    require_manager(project, actor)
    validate_stage(project, data)
    stage = Stage.objects.create(project=project, created_by=actor, **data)
    record_event(project, actor, "stage.created", stage_id=stage.pk, name=stage.name)
    return stage


@atomic_operation
def update_stage(*, stage, actor, **data):
    project = lock_project(stage.project)
    require_manager(project, actor)
    stage.refresh_from_db()
    validate_stage(project, data, stage)
    if stage.status == Stage.Status.PLANNED and data.get("status") == Stage.Status.IN_PROGRESS and project.status == project.Status.PLANNING:
        # ТЗ 4.2: works begin only after the estimate is approved and the customer starts the project.
        raise ValidationError({"status": "Работы по проекту ещё не запущены. Утвердите смету и нажмите «Запустить работы» на странице сметы."})
    before = {key: str(getattr(stage, key)) for key in data}
    for key, value in data.items():
        setattr(stage, key, value)
    stage.save()
    record_event(project, actor, "stage.updated", stage_id=stage.pk, name=stage.name, before=before, after={key: str(value) for key, value in data.items()})
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
