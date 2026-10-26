from functools import wraps

from django.db import IntegrityError, transaction
from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Project, ProjectEvent, ProjectInvitation, ProjectMember, SavedContact
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
def create_project(*, user, name, address="", description=""):
    project = Project.objects.create(name=name, address=address, description=description, created_by=user)
    ProjectMember.objects.create(project=project, user=user, role=ProjectMember.Role.CUSTOMER)
    record_event(project, user, "project.created")
    return project


@atomic_operation
def update_project(*, project, actor, **data):
    project = lock_project(project)
    require_customer(project, actor)
    before = {key: getattr(project, key) for key in data}
    for key, value in data.items():
        if key not in {"name", "address", "description"}:
            raise ValidationError("Недопустимое поле проекта.")
        setattr(project, key, value)
    project.save()
    record_event(project, actor, "project.updated", before=before, after=data)
    return project


def remember_contact(owner, email, name=""):
    contact, _ = SavedContact.objects.get_or_create(owner=owner, email=email.strip().lower())
    if name:
        contact.name = name
    contact.save()
    return contact


@atomic_operation
def add_member(*, project, actor, email="", contact_id=None, role="executor"):
    project = lock_project(project)
    require_customer(project, actor)
    if bool(email) == bool(contact_id):
        raise ValidationError("Выберите контакт из списка или укажите email.")
    if contact_id:
        contact = get_object_or_404(SavedContact, owner=actor, pk=contact_id)
        email = contact.email
    email = email.strip().lower()
    try:
        validate_email(email)
    except DjangoValidationError:
        raise ValidationError({"email": "Укажите корректный email."})
    if role not in ["foreman", "executor"]:
        raise ValidationError({"role": "Выберите прораба или исполнителя."})
    expire_invitations(project)
    if project.members.filter(user__email__iexact=email).exists():
        raise ValidationError({"email": "Пользователь уже участвует в проекте."})
    if project.invitations.filter(email__iexact=email, status="pending").exists():
        raise ValidationError({"email": "Для этого email уже есть ожидающее приглашение ниже."})
    if role == "foreman" and (project.members.filter(role="foreman").exists() or project.invitations.filter(role="foreman", status="pending").exists()):
        raise ValidationError({"role": "Прораб уже назначен или приглашён в проект."})
    user = get_user_model().objects.filter(email__iexact=email, is_active=True, is_email_verified=True).first()
    if role == "foreman" or user is None:
        return invite_member(project=project, actor=actor, email=email, role=role)
    member = ProjectMember.objects.create(project=project, user=user, role=role)
    remember_contact(actor, email, user.get_full_name())
    record_event(project, actor, "member.added", member_id=member.pk, email=email, role=role)
    return member


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
    remember_contact(actor, email)
    record_event(project, actor, "invitation.created", invitation_id=invitation.pk, email=email, role=role)
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
    remember_contact(invitation.invited_by, invitation.email, user.get_full_name())
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
    if role == "foreman":
        raise ValidationError("Прораб должен принять приглашение. Сначала удалите пользователя из исполнителей, затем пригласите его как прораба.")
    if member.responsible_stages.exists():
        raise ValidationError("Сначала переназначьте ответственного в этапах.")
    if member.assigned_tasks.exists() or member.assigned_remarks.exists():
        raise ValidationError("Участник связан с задачами или замечаниями. Сначала переназначьте задачи; историю замечаний необходимо сохранить.")
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
        raise ValidationError("Участник связан с задачами или замечаниями. Сначала переназначьте задачи; историю замечаний необходимо сохранить.")
    record_event(project, actor, "member.removed", member_id=member.pk, user_id=member.user_id, email=member.user.email, role=member.role)
    remember_contact(actor, member.user.email, member.user.get_full_name())
    member.delete()
