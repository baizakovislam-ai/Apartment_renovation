from rest_framework.exceptions import PermissionDenied
from .models import ProjectMember


def require_member(project, user, roles=None):
    member = ProjectMember.objects.filter(project=project, user=user).first()
    if member is None or (roles is not None and member.role not in roles):
        raise PermissionDenied("Недостаточно прав для действия в этом проекте.")
    return member


def require_customer(project, user):
    return require_member(project, user, [ProjectMember.Role.CUSTOMER])


def require_manager(project, user):
    return require_member(project, user, [ProjectMember.Role.CUSTOMER, ProjectMember.Role.FOREMAN])
