from rest_framework.exceptions import PermissionDenied
from .models import ProjectMember


def active_membership(project, user):
    """The user's current membership; a removed member (is_active=False) has no access."""
    if user is None or not user.is_authenticated:
        return None
    return ProjectMember.objects.filter(project=project, user=user, is_active=True).first()


def require_member(project, user, roles=None):
    member = active_membership(project, user)
    if member is None or (roles is not None and member.role not in roles):
        raise PermissionDenied("Недостаточно прав для действия в этом проекте.")
    return member


def require_customer(project, user):
    return require_member(project, user, [ProjectMember.Role.CUSTOMER])


def require_manager(project, user):
    return require_member(project, user, [ProjectMember.Role.CUSTOMER, ProjectMember.Role.FOREMAN])
