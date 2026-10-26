from functools import wraps

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Project, ProjectEvent, ProjectInvitation, ProjectMember
from .permissions import require_customer


def atomic_operation(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        try:
            with transaction.atomic():
                return func(*args, **kwargs)
        except IntegrityError as error:
            raise ValidationError("Конфликт данных: участник или приглашение уже существует, либо роль занята.") from error
    return wrapped


def lock_project(project):
    return Project.objects.select_for_update().get(pk=project.pk)


def record_event(project, actor, action, **data):
    ProjectEvent.objects.create(project=project, actor=actor, action=action, data=data)


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


@atomic_operation
def delete_project(*, project, actor):
    project = lock_project(project)
    require_customer(project, actor)
    if project.created_by_id != actor.pk or project.status != Project.Status.PLANNING:
        raise ValidationError("Удалить можно только собственный черновик проекта.")
    if project.stages.exists() or project.tasks.exists():
        raise ValidationError("В проекте уже есть этапы или задачи. Удаление запрещено.")
    project.delete()


def expire_invitations(project):
    ProjectInvitation.objects.filter(project=project, status="pending", expires_at__lte=timezone.now()).update(status="expired")


@atomic_operation
def invite_member(*, project, actor, email, role):
    project = lock_project(project)
    require_customer(project, actor)
    expire_invitations(project)
    email = email.strip().lower()
    if role not in ["foreman", "executor"]:
        raise ValidationError({"role": "Можно пригласить прораба или исполнителя."})
    if project.members.filter(user__email__iexact=email).exists():
        raise ValidationError({"email": "Пользователь уже участвует в проекте."})
    if role == "foreman" and project.members.filter(role="foreman").exists():
        raise ValidationError({"role": "В проекте уже есть прораб."})
    invitation = ProjectInvitation.objects.create(project=project, email=email, role=role, invited_by=actor)
    record_event(project, actor, "invitation.created", invitation_id=invitation.pk, role=role)
    return invitation


@atomic_operation
def accept_invitation(*, token, user):
    invitation = get_object_or_404(ProjectInvitation, token=token)
    project = lock_project(invitation.project)
    invitation.refresh_from_db()
    if not user.email or user.email.strip().lower() != invitation.email.lower():
        raise ValidationError("Приглашение предназначено для другого email.")
    if invitation.status != "pending":
        raise ValidationError("Приглашение уже использовано или отменено.")
    if invitation.expires_at <= timezone.now():
        raise ValidationError("Срок действия приглашения истёк.")
    if project.members.filter(user=user).exists():
        raise ValidationError("Вы уже участвуете в проекте.")
    member = ProjectMember.objects.create(project=project, user=user, role=invitation.role)
    invitation.status = "accepted"
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["status", "accepted_at"])
    record_event(project, user, "invitation.accepted", member_id=member.pk, role=member.role)
    return member


@atomic_operation
def cancel_invitation(*, project, actor, invitation_id):
    project = lock_project(project)
    require_customer(project, actor)
    invitation = get_object_or_404(ProjectInvitation, project=project, pk=invitation_id)
    if invitation.status != "pending":
        raise ValidationError("Можно отменить только ожидающее приглашение.")
    invitation.status = "cancelled"
    invitation.save(update_fields=["status"])
    record_event(project, actor, "invitation.cancelled", invitation_id=invitation.pk)


@atomic_operation
def change_member_role(*, project, actor, member_id, role):
    project = lock_project(project)
    require_customer(project, actor)
    member = get_object_or_404(ProjectMember, project=project, pk=member_id)
    if member.role == "customer" or role not in ["foreman", "executor"]:
        raise ValidationError("Изменение роли заказчика не поддерживается.")
    if member.role == role:
        return member
    if member.responsible_stages.exists():
        raise ValidationError("Сначала переназначьте ответственного в этапах.")
    if member.assigned_tasks.exists() or member.assigned_remarks.exclude(status="closed").exists():
        raise ValidationError("У участника есть задачи или открытые замечания. Сначала переназначьте их.")
    if role == "foreman":
        expire_invitations(project)
        if project.invitations.filter(status="pending", role="foreman").exists():
            raise ValidationError("Сначала отмените ожидающее приглашение прорабу.")
    old_role = member.role
    member.role = role
    member.save(update_fields=["role"])
    record_event(project, actor, "member.role_changed", member_id=member.pk, old_role=old_role, role=role)
    return member


@atomic_operation
def remove_member(*, project, actor, member_id):
    project = lock_project(project)
    require_customer(project, actor)
    member = get_object_or_404(ProjectMember, project=project, pk=member_id)
    if member.role == "customer":
        raise ValidationError("Нельзя удалить заказчика проекта.")
    if member.responsible_stages.exists():
        raise ValidationError("Сначала переназначьте ответственного в этапах.")
    if member.assigned_tasks.exists() or member.assigned_remarks.exists():
        raise ValidationError("За участником закреплены задачи или замечания. Удаление запрещено, сначала переназначьте их.")
    record_event(project, actor, "member.removed", member_id=member.pk, user_id=member.user_id, role=member.role)
    member.delete()
