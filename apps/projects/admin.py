from django.contrib import admin
from .models import Project, ProjectEvent, ProjectInvitation, ProjectMember


@admin.register(Project, ProjectMember, ProjectInvitation, ProjectEvent)
class ProjectDataAdmin(admin.ModelAdmin):
    """Read-only inspection: mutations go through services to preserve invariants."""
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
