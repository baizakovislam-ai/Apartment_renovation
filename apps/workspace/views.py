from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from apps.projects import services as project_services
from apps.projects.models import Project, ProjectInvitation, ProjectMember
from apps.projects.permissions import require_customer, require_manager
from apps.repairs import services as stage_services
from apps.repairs import task_services
from .forms import InvitationForm, ProjectForm, StageForm


def own_projects(user):
    return Project.objects.filter(pk__in=ProjectMember.objects.filter(user=user).values("project_id")).annotate(
        stage_count=Count("stages", distinct=True), member_count=Count("members", distinct=True), task_count=Count("tasks", distinct=True))


def own_project(request, project_id):
    return get_object_or_404(own_projects(request.user), pk=project_id)


def context(request, project=None, page="dashboard", **extra):
    membership = project.members.get(user=request.user) if project else None
    return {"projects": own_projects(request.user), "project": project, "membership": membership,
            "is_customer": bool(membership and membership.role == "customer"),
            "is_manager": bool(membership and membership.role in ["customer", "foreman"]),
            "page": page, "today": timezone.localdate(), **extra}


def flatten_errors(detail):
    if isinstance(detail, dict):
        return " ".join(flatten_errors(value) for value in detail.values())
    if isinstance(detail, (list, tuple)):
        return " ".join(flatten_errors(value) for value in detail)
    return str(detail)


def form_service_error(form, error):
    form.add_error(None, flatten_errors(error.detail))


def check_permission(check, project, user):
    try:
        check(project, user)
    except APIException:
        # Match API isolation: unrelated records never appear in the workspace.
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Недостаточно прав для этого действия.")


@login_required
def dashboard(request):
    projects = own_projects(request.user)
    project_id = request.GET.get("project")
    project = get_object_or_404(projects, pk=project_id) if project_id and project_id.isdigit() else projects.first()
    stages = list(project.stages.select_related("responsible__user")) if project else []
    completed = sum(stage.status in ["completed", "pending_acceptance", "accepted"] for stage in stages)
    progress = task_services.project_progress(project) if project else None
    open_tasks = task_services.visible_tasks(project, request.user).exclude(status="done") if project else []
    return render(request, "workspace/dashboard.html", context(request, project, stages=stages[:5], total_stages=len(stages),
        completed_stages=completed, progress=progress, upcoming=list(open_tasks[:5]),
        open_task_count=open_tasks.count() if project else 0,
        my_open_tasks=open_tasks.filter(assignee__user=request.user).count() if project else 0))


@login_required
def project_list(request):
    projects = own_projects(request.user)
    query = request.GET.get("q", "").strip()
    if query:
        projects = projects.filter(name__icontains=query)
    memberships = {m.project_id: m.get_role_display() for m in ProjectMember.objects.filter(user=request.user)}
    items = [{"project": p, "role": memberships[p.pk]} for p in projects]
    return render(request, "workspace/projects.html", context(request, page="projects", items=items, query=query))


@login_required
def project_create(request):
    form = ProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            project = project_services.create_project(user=request.user, **form.cleaned_data)
        except APIException as error:
            form_service_error(form, error)
            return render(request, "workspace/form.html", context(request, page="projects", form=form, title="Новый проект", subtitle="Название обязательно, остальные поля можно заполнить позже.", section="проекта"))
        messages.success(request, "Проект создан. Теперь пригласите команду и добавьте этапы.")
        return redirect("workspace:members", project_id=project.pk)
    return render(request, "workspace/form.html", context(request, page="projects", form=form, title="Новый проект", subtitle="Название обязательно, остальные поля можно заполнить позже.", section="проекта"))


@login_required
def stage_list(request, project_id):
    project = own_project(request, project_id)
    stages = project.stages.select_related("responsible__user").annotate(
        task_total=Count("tasks", distinct=True), task_done=Count("tasks", filter=Q(tasks__status="done"), distinct=True))
    query = request.GET.get("q", "").strip()
    state = request.GET.get("status", "")
    if query:
        stages = stages.filter(name__icontains=query)
    if state:
        stages = stages.filter(status=state)
    from apps.repairs.models import Stage
    return render(request, "workspace/stages.html", context(request, project, "stages", stages=stages, query=query, status_filter=state, stage_statuses=Stage.Status.choices))


@login_required
def stage_form(request, project_id, stage_id=None):
    project = own_project(request, project_id)
    check_permission(require_manager, project, request.user)
    stage = get_object_or_404(project.stages, pk=stage_id) if stage_id else None
    if stage and stage.status in ["accepted", "pending_acceptance", "cancelled"]:
        messages.error(request, "Этот этап закрыт для редактирования.")
        return redirect("workspace:stages", project_id=project.pk)
    form = StageForm(request.POST or None, project=project, stage=stage)
    if request.method == "POST" and form.is_valid():
        try:
            if stage:
                stage_services.update_stage(stage=stage, actor=request.user, **form.cleaned_data)
            else:
                stage_services.create_stage(project=project, actor=request.user, **form.cleaned_data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Этап сохранён.")
            return redirect("workspace:stages", project_id=project.pk)
    return render(request, "workspace/form.html", context(request, project, "stages", form=form, title="Редактирование этапа" if stage else "Новый этап", subtitle="Укажите сроки работ и ответственного из команды проекта.", stage=stage, section="этапа", cancel_url="workspace:stages"))


@login_required
@require_POST
def stage_delete(request, project_id, stage_id):
    project = own_project(request, project_id)
    stage = get_object_or_404(project.stages, pk=stage_id)
    try:
        stage_services.delete_stage(stage=stage, actor=request.user)
    except APIException as error:
        messages.error(request, flatten_errors(error.detail))
    else:
        messages.success(request, "Черновик этапа удалён.")
    return redirect("workspace:stages", project_id=project.pk)


@login_required
def members(request, project_id):
    project = own_project(request, project_id)
    membership = project.members.get(user=request.user)
    form = InvitationForm(request.POST or None)
    if request.method == "POST":
        check_permission(require_customer, project, request.user)
        if form.is_valid():
            try:
                project_services.invite_member(project=project, actor=request.user, **form.cleaned_data)
            except APIException as error:
                form_service_error(form, error)
            else:
                messages.success(request, "Приглашение создано. Передайте ссылку участнику.")
                return redirect("workspace:members", project_id=project.pk)
    invitations = list(project.invitations.all()) if membership.role == "customer" else []
    for invitation in invitations:
        invitation.accept_url = request.build_absolute_uri(reverse("workspace:accept", args=[invitation.token]))
        invitation.expired = invitation.status == "pending" and invitation.expires_at <= timezone.now()
    return render(request, "workspace/members.html", context(request, project, "members", team=project.members.select_related("user"), invitations=invitations, form=form))


@login_required
@require_POST
def member_action(request, project_id, member_id):
    project = own_project(request, project_id)
    action = request.POST.get("action")
    try:
        if action == "remove":
            project_services.remove_member(project=project, actor=request.user, member_id=member_id)
        elif action == "role":
            project_services.change_member_role(project=project, actor=request.user, member_id=member_id, role=request.POST.get("role"))
        else:
            raise Http404
    except APIException as error:
        messages.error(request, flatten_errors(error.detail))
    else:
        messages.success(request, "Участник обновлён.")
    return redirect("workspace:members", project_id=project.pk)


@login_required
@require_POST
def invitation_cancel(request, project_id, invitation_id):
    project = own_project(request, project_id)
    try:
        project_services.cancel_invitation(project=project, actor=request.user, invitation_id=invitation_id)
    except APIException as error:
        messages.error(request, flatten_errors(error.detail))
    else:
        messages.success(request, "Приглашение отменено.")
    return redirect("workspace:members", project_id=project.pk)


@login_required
def accept(request, token):
    invitation = get_object_or_404(ProjectInvitation.objects.select_related("project"), token=token, email__iexact=request.user.email)
    error = None
    if request.method == "POST":
        try:
            member = project_services.accept_invitation(token=token, user=request.user)
        except APIException as exc:
            error = flatten_errors(exc.detail)
        else:
            messages.success(request, "Приглашение принято. Вы присоединились к проекту.")
            return redirect("workspace:stages", project_id=member.project_id)
    available = invitation.status == "pending" and invitation.expires_at > timezone.now()
    return render(request, "workspace/accept.html", context(request, page="projects", invitation=invitation, available=available, error=error))


@login_required
def history(request, project_id):
    project = own_project(request, project_id)
    check_permission(require_manager, project, request.user)
    return render(request, "workspace/history.html", context(request, project, "history", events=project.events.select_related("actor")[:100]))
