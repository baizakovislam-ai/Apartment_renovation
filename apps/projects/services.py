from functools import wraps

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import Project, ProjectEvent, ProjectInvitation, ProjectMember
from .permissions import require_customer, require_manager

ARCHIVED_MESSAGE = "Проект в архиве. Верните его из архива, чтобы вносить изменения."


def atomic_operation(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        try:
            with transaction.atomic():
                return func(*args, **kwargs)
        except IntegrityError as error:
            raise ValidationError("Конфликт данных: участник или приглашение уже существует, либо роль занята.") from error
    return wrapped


def lock_project(project, allow_archived=False):
    """Lock the project row for the transaction; archived projects are read-only."""
    project = Project.objects.select_for_update().get(pk=project.pk)
    if project.archived_at is not None and not allow_archived:
        raise ValidationError(ARCHIVED_MESSAGE)
    return project


def record_event(project, actor, action, **data):
    ProjectEvent.objects.create(project=project, actor=actor, action=action, data=data)


def active_members(project):
    return project.members.filter(is_active=True)


# ---------- Project (FR02) ----------

PROJECT_FIELDS = ["name", "address", "area", "start_date", "end_date", "budget", "description"]


def validate_project(data, instance=None):
    unknown = set(data) - set(PROJECT_FIELDS)
    if unknown:
        raise ValidationError(f"Неизвестные поля проекта: {', '.join(sorted(unknown))}.")
    if "name" in data and not str(data["name"]).strip():
        raise ValidationError({"name": "Укажите название проекта."})
    start = data.get("start_date", instance.start_date if instance else None)
    end = data.get("end_date", instance.end_date if instance else None)
    if start and end and end < start:
        raise ValidationError({"end_date": "Окончание ремонта не может быть раньше начала."})
    if data.get("area") is not None and data["area"] <= 0:
        raise ValidationError({"area": "Площадь должна быть больше нуля."})
    if data.get("budget") is not None and data["budget"] < 0:
        raise ValidationError({"budget": "Бюджет не может быть отрицательным."})


@atomic_operation
def create_project(*, user, **data):
    validate_project(data)
    if "name" in data:
        data["name"] = data["name"].strip()
    project = Project.objects.create(created_by=user, **data)
    ProjectMember.objects.create(project=project, user=user, role=ProjectMember.Role.CUSTOMER)
    record_event(project, user, "project.created", name=project.name)
    return project


@atomic_operation
def update_project(*, project, actor, **data):
    project = lock_project(project)
    require_customer(project, actor)
    if project.status == Project.Status.COMPLETED:
        raise ValidationError("Завершённый проект нельзя изменить.")
    validate_project(data, project)
    changed = {key: value for key, value in data.items() if getattr(project, key) != value}
    if not changed:
        return project
    before = {key: str(getattr(project, key)) for key in changed}
    for key, value in changed.items():
        setattr(project, key, value)
    project.save()
    record_event(project, actor, "project.updated", name=project.name, before=before, after={key: str(value) for key, value in changed.items()})
    return project


def project_delete_blockers(project):
    """Work records that must never be erased (ТЗ 4.12). Team, invitations and empty stages may go with the project."""
    reasons = []
    if project.tasks.exists():
        reasons.append("в проекте есть задачи")
    if project.photo_reports.exists():
        reasons.append("есть фотоотчёты")
    if project.remarks.exists():
        reasons.append("есть замечания")
    if project.stage_decisions.exists():
        reasons.append("этапы уже передавались на приёмку")
    return reasons


@atomic_operation
def delete_project(*, project, actor):
    project = lock_project(project, allow_archived=True)
    require_customer(project, actor)
    if project.created_by_id != actor.pk:
        raise ValidationError("Удалить проект может только заказчик, который его создал.")
    blockers = project_delete_blockers(project)
    if blockers:
        raise ValidationError("Проект нельзя удалить безвозвратно: " + ", ".join(blockers) + ". Отправьте его в архив.")
    # Stages are protected from cascading, so remove the (empty) stages first; members, invitations and history follow the project.
    project.stages.all().delete()
    project.delete()


@atomic_operation
def archive_project(*, project, actor, reason):
    project = lock_project(project)
    require_customer(project, actor)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Укажите причину, по которой проект отправляется в архив."})
    project.archived_at = timezone.now()
    project.archive_reason = reason
    project.save(update_fields=["archived_at", "archive_reason", "updated_at"])
    record_event(project, actor, "project.archived", name=project.name, reason=reason)
    return project


@atomic_operation
def restore_project(*, project, actor):
    project = lock_project(project, allow_archived=True)
    require_customer(project, actor)
    if project.archived_at is None:
        raise ValidationError("Проект не находится в архиве.")
    project.archived_at = None
    project.archive_reason = ""
    project.save(update_fields=["archived_at", "archive_reason", "updated_at"])
    record_event(project, actor, "project.restored", name=project.name)
    return project


# ---------- Invitations (FR03) ----------

def expire_invitations(project):
    ProjectInvitation.objects.filter(project=project, status="pending", expires_at__lte=timezone.now()).update(status="expired")


@atomic_operation
def invite_member(*, project, actor, email, role):
    """The customer invites a foreman or executors; the foreman may invite executors (ТЗ: прораб назначает исполнителей)."""
    project = lock_project(project)
    inviter = require_manager(project, actor)
    expire_invitations(project)
    email = email.strip().lower()
    if role not in ["foreman", "executor"]:
        raise ValidationError({"role": "Можно пригласить прораба или исполнителя."})
    if inviter.role == ProjectMember.Role.FOREMAN and role != ProjectMember.Role.EXECUTOR:
        raise PermissionDenied("Прораб может приглашать только исполнителей.")
    if active_members(project).filter(user__email__iexact=email).exists():
        raise ValidationError({"email": "Этот пользователь уже участвует в проекте."})
    if project.invitations.filter(status="pending", email__iexact=email).exists():
        raise ValidationError({"email": "Этому email уже отправлено приглашение. Отмените его, если нужно пригласить заново."})
    if role == "foreman":
        if active_members(project).filter(role="foreman").exists():
            raise ValidationError({"role": "В проекте уже есть прораб. Сначала удалите текущего прораба."})
        if project.invitations.filter(status="pending", role="foreman").exists():
            raise ValidationError({"role": "Приглашение прорабу уже отправлено. Отмените его, чтобы пригласить другого."})
    invitation = ProjectInvitation.objects.create(project=project, email=email, role=role, invited_by=actor)
    record_event(project, actor, "invitation.created", invitation_id=invitation.pk, role=role, email=email)
    return invitation


def lock_invitation(token):
    invitation = get_object_or_404(ProjectInvitation, token=token)
    project = lock_project(invitation.project)
    invitation = ProjectInvitation.objects.select_for_update().get(pk=invitation.pk)
    return invitation, project


def check_invitation_owner(invitation, user):
    if not user.email or user.email.strip().lower() != invitation.email.lower():
        raise ValidationError(f"Приглашение отправлено на {invitation.email}. Войдите под этим email, чтобы ответить на него.")
    if invitation.status != "pending":
        raise ValidationError("Приглашение уже использовано, отклонено или отменено.")
    if invitation.expires_at <= timezone.now():
        raise ValidationError("Срок действия приглашения истёк. Попросите прислать новое.")


@atomic_operation
def accept_invitation(*, token, user):
    invitation, project = lock_invitation(token)
    check_invitation_owner(invitation, user)
    member = ProjectMember.objects.filter(project=project, user=user).first()
    if member and member.is_active:
        raise ValidationError("Вы уже участвуете в проекте.")
    if invitation.role == "foreman" and active_members(project).filter(role="foreman").exists():
        raise ValidationError("В проекте уже есть прораб.")
    if member:
        # Re-joining after removal: the old row is reused so earlier work stays linked to the same membership.
        member.role, member.is_active, member.removed_at, member.removed_by = invitation.role, True, None, None
        member.save(update_fields=["role", "is_active", "removed_at", "removed_by"])
    else:
        member = ProjectMember.objects.create(project=project, user=user, role=invitation.role)
    invitation.status = "accepted"
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["status", "accepted_at"])
    record_event(project, user, "invitation.accepted", member_id=member.pk, role=member.role)
    return member


@atomic_operation
def decline_invitation(*, token, user):
    invitation, project = lock_invitation(token)
    check_invitation_owner(invitation, user)
    invitation.status = "declined"
    invitation.save(update_fields=["status"])
    record_event(project, user, "invitation.declined", invitation_id=invitation.pk)
    return invitation


@atomic_operation
def cancel_invitation(*, project, actor, invitation_id):
    project = lock_project(project)
    member = require_manager(project, actor)
    invitation = get_object_or_404(ProjectInvitation, project=project, pk=invitation_id)
    if member.role == ProjectMember.Role.FOREMAN and invitation.invited_by_id != actor.pk:
        raise PermissionDenied("Прораб может отменять только свои приглашения.")
    if invitation.status != "pending":
        raise ValidationError("Можно отменить только ожидающее приглашение.")
    invitation.status = "cancelled"
    invitation.save(update_fields=["status"])
    record_event(project, actor, "invitation.cancelled", invitation_id=invitation.pk, email=invitation.email)


def pending_invitations_for(user):
    """Invitations waiting for this account, shown on the dashboard so a link is not required."""
    if not user.is_authenticated or not user.email:
        return ProjectInvitation.objects.none()
    return ProjectInvitation.objects.filter(email__iexact=user.email, status="pending", expires_at__gt=timezone.now(),
                                            project__archived_at__isnull=True).select_related("project", "invited_by")


# ---------- Team members (FR03) ----------

def unfinished_work(member):
    """Work that would be orphaned if the member left: it has to be handed over first."""
    from apps.quality.models import Remark
    from apps.repairs.models import Stage, Task
    return {
        "tasks": list(member.assigned_tasks.exclude(status=Task.Status.DONE)),
        "stages": list(member.responsible_stages.exclude(status__in=[Stage.Status.ACCEPTED, Stage.Status.CANCELLED])),
        "remarks": list(member.assigned_remarks.filter(status__in=[Remark.Status.OPEN, Remark.Status.FIXED])),
    }


def describe_work(work):
    parts = []
    if work["tasks"]:
        parts.append(f"задач: {len(work['tasks'])}")
    if work["stages"]:
        parts.append(f"этапов под ответственностью: {len(work['stages'])}")
    if work["remarks"]:
        parts.append(f"замечаний: {len(work['remarks'])}")
    return ", ".join(parts)


def hand_over_work(project, actor, member, replacement, work):
    from apps.repairs.models import Task
    if work["tasks"] and replacement.role != ProjectMember.Role.EXECUTOR:
        raise ValidationError({"replacement": "Незавершённые задачи можно передать только исполнителю."})
    if work["remarks"] and replacement.role == ProjectMember.Role.CUSTOMER:
        raise ValidationError({"replacement": "Замечания можно передать прорабу или исполнителю."})
    for task in work["tasks"]:
        task.assignee = replacement
        if task.status == Task.Status.IN_PROGRESS:
            task.status = Task.Status.TODO  # the new executor starts the work themselves
        task.version += 1
        task.save(update_fields=["assignee", "status", "version", "updated_at"])
        record_event(project, actor, "task.updated", task_id=task.pk, name=task.name,
                     before={"assignee": member.user.get_full_name() or member.user.email},
                     after={"assignee": replacement.user.get_full_name() or replacement.user.email})
    for stage in work["stages"]:
        stage.responsible = replacement
        stage.save(update_fields=["responsible", "updated_at"])
        record_event(project, actor, "stage.updated", stage_id=stage.pk, name=stage.name,
                     after={"responsible": replacement.user.get_full_name() or replacement.user.email})
    for remark in work["remarks"]:
        remark.responsible = replacement
        remark.version += 1
        remark.save(update_fields=["responsible", "version"])


@atomic_operation
def change_member_role(*, project, actor, member_id, role):
    project = lock_project(project)
    require_customer(project, actor)
    member = get_object_or_404(ProjectMember, project=project, pk=member_id, is_active=True)
    if member.role == "customer" or role not in ["foreman", "executor"]:
        raise ValidationError("Изменение роли заказчика не поддерживается.")
    if member.role == role:
        return member
    if member.role == "executor" and unfinished_work(member)["tasks"]:
        raise ValidationError("У исполнителя есть незавершённые задачи. Задачи назначаются только исполнителям — сначала передайте их другому.")
    if role == "foreman":
        expire_invitations(project)
        if active_members(project).filter(role="foreman").exists():
            raise ValidationError("В проекте уже есть прораб.")
        if project.invitations.filter(status="pending", role="foreman").exists():
            raise ValidationError("Сначала отмените ожидающее приглашение прорабу.")
    old_role = member.role
    member.role = role
    member.save(update_fields=["role"])
    record_event(project, actor, "member.role_changed", member_id=member.pk, old_role=old_role, role=role,
                 name=member.user.get_full_name() or member.user.email)
    return member


@atomic_operation
def remove_member(*, project, actor, member_id, replacement_id=None):
    """Remove a member from the team. Finished work stays attributed to them; unfinished work is handed over."""
    project = lock_project(project)
    require_customer(project, actor)
    member = get_object_or_404(ProjectMember, project=project, pk=member_id, is_active=True)
    if member.role == "customer":
        raise ValidationError("Нельзя удалить заказчика проекта.")
    work = unfinished_work(member)
    if any(work.values()):
        if not replacement_id:
            raise ValidationError({"replacement": f"У участника есть незавершённая работа ({describe_work(work)}). Выберите, кому её передать."})
        replacement = active_members(project).filter(pk=replacement_id).exclude(pk=member.pk).first()
        if replacement is None:
            raise ValidationError({"replacement": "Передать работу можно только действующему участнику этого проекта."})
        hand_over_work(project, actor, member, replacement, work)
    member.is_active = False
    member.removed_at = timezone.now()
    member.removed_by = actor
    member.save(update_fields=["is_active", "removed_at", "removed_by"])
    record_event(project, actor, "member.removed", member_id=member.pk, user_id=member.user_id, role=member.role,
                 name=member.user.get_full_name() or member.user.email)
    return member
