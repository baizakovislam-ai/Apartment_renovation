"""Test helpers shared by several apps."""
from apps.projects.models import Project


def mark_started(project):
    """Put a test project straight into «В работе», as if the estimate were approved and works started.

    Tests about stages, tasks and remarks use this; the start rule itself is covered in apps/estimates/tests.py.
    """
    Project.objects.filter(pk=project.pk).update(status=Project.Status.IN_PROGRESS)
    project.status = Project.Status.IN_PROGRESS
    return project
