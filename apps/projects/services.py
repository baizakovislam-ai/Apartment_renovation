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


@atomic_operation
def create_project(*, user, name):
    project = Project.objects.create(name=name, created_by=user)
    ProjectMember.objects.create(project=project, user=user, role=ProjectMember.Role.CUSTOMER)
    record_event(project, user, "project.created")
    return project


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
    record_event(project, actor, "member.removed", member_id=member.pk, user_id=member.user_id, role=member.role)
    member.delete()
