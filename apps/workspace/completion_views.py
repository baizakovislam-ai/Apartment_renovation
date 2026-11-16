"""Completion action for customer-owned renovation projects (FR18)."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from apps.projects import completion as completion_service
from .views import own_project, run_service


@login_required
@require_POST
def complete_project(request, project_id):
    project = own_project(request, project_id)
    run_service(
        request,
        lambda: completion_service.complete_project(project=project, actor=request.user),
        "Проект завершён. Все этапы приняты, нерешённых замечаний нет.",
    )
    return redirect("workspace:project", project_id=project.pk)
