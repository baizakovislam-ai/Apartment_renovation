"""Project completion rules (ТЗ FR18)."""
from rest_framework.exceptions import ValidationError

from apps.projects.models import Project
from apps.projects.permissions import require_customer
from apps.projects.services import atomic_operation, lock_project, record_event
from apps.quality.models import Remark
from apps.repairs.models import Stage


@atomic_operation
def complete_project(*, project, actor):
    """Complete a project only after every non-cancelled stage is accepted."""
    project = lock_project(project)
    require_customer(project, actor)
    if project.status == Project.Status.COMPLETED:
        raise ValidationError("Проект уже завершён.")
    if project.status != Project.Status.IN_PROGRESS:
        raise ValidationError("Завершить можно только проект, в котором идут работы.")

    stages = project.stages.exclude(status=Stage.Status.CANCELLED)
    total = stages.count()
    if not total:
        raise ValidationError("Нельзя завершить проект без этапов ремонта.")
    unaccepted = stages.exclude(status=Stage.Status.ACCEPTED).count()
    if unaccepted:
        raise ValidationError(f"Нельзя завершить проект: не приняты этапы ({unaccepted}).")

    unresolved = project.remarks.filter(status__in=[Remark.Status.OPEN, Remark.Status.FIXED]).count()
    if unresolved:
        raise ValidationError(f"Нельзя завершить проект: осталось нерешённых замечаний ({unresolved}).")

    project.status = Project.Status.COMPLETED
    project.save(update_fields=["status", "updated_at"])
    record_event(project, actor, "project.completed", stages=total)
    return project
