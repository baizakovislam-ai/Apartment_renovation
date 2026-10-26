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
from apps.repairs.task_services import project_progress
from .forms import MemberForm, ProjectForm, QuickStageForm, StageForm


def own_projects(user):
    return Project.objects.filter(pk__in=ProjectMember.objects.filter(user=user).values("project_id")).annotate(stage_count=Count("stages", distinct=True), member_count=Count("members", distinct=True))


def own_project(request, project_id):
    return get_object_or_404(own_projects(request.user), pk=project_id)


def context(request, project=None, page="dashboard", **extra):
    membership = project.members.get(user=request.user) if project else None
    return {"projects": own_projects(request.user), "project": project, "membership": membership,
            "is_customer": bool(membership and membership.role == "customer"),
            "is_foreman": bool(membership and membership.role == "foreman"),
            "is_executor": bool(membership and membership.role == "executor"),
            "is_manager": bool(membership and membership.role in ["customer", "foreman"]),
            "incoming_invitations": ProjectInvitation.objects.filter(email__iexact=request.user.email, status="pending", expires_at__gt=timezone.now()).select_related("project") if page in ["dashboard", "projects"] else [],
            "page": page, "today": timezone.localdate(), "progress": project_progress(project) if project else None, **extra}


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
    if project_id and project_id.isdigit():
        project = get_object_or_404(projects, pk=project_id)
        return redirect("workspace:project-detail", project_id=project.pk)
    project = projects.first()
    stages = list(project.stages.select_related("responsible__user")) if project else []
    completed = sum(stage.status in ["completed", "pending_acceptance", "accepted"] for stage in stages)
    upcoming = sorted([stage for stage in stages if stage.end_date and stage.status in ["planned", "in_progress"]], key=lambda stage: stage.end_date)
    return render(request, "workspace/dashboard.html", context(request, project, stages=stages[:5], total_stages=len(stages),
        completed_stages=completed, overdue=sum(stage.end_date < timezone.localdate() for stage in upcoming), upcoming=upcoming[:4]))


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
        project = project_services.create_project(user=request.user, **form.cleaned_data)
        messages.success(request, "Проект создан. Добавьте этапы ремонта и команду прямо здесь.")
        return redirect("workspace:project-detail", project_id=project.pk)
    return render(request, "workspace/form.html", context(request, page="projects", form=form, title="Новый проект", subtitle="В новом проекте вы станете заказчиком: сможете управлять объектом и приглашать команду. В других проектах ваша роль сохранится.", back_url="workspace:projects"))


@login_required
def project_detail(request, project_id):
    project = own_project(request, project_id)
    stages = project.stages.select_related("responsible__user")
    return render(request, "workspace/project_detail.html", context(request, project, "project-detail",
        stages=stages, completed=stages.filter(status__in=["completed", "pending_acceptance", "accepted"]).count(),
        accepted=stages.filter(status="accepted").count(),
        missing_schedule=stages.filter(Q(start_date__isnull=True) | Q(end_date__isnull=True)).count(),
        missing_responsible=stages.filter(responsible__isnull=True).count(),
        foreman=project.members.select_related("user").filter(role="foreman").first(),
        foreman_pending=project.invitations.filter(role="foreman", status="pending", expires_at__gt=timezone.now()).exists(),
        team=project.members.select_related("user"), form=QuickStageForm()))


@login_required
def project_edit(request, project_id):
    project = own_project(request, project_id)
    check_permission(require_customer, project, request.user)
    form = ProjectForm(request.POST or None, initial={field: getattr(project, field) for field in ProjectForm.base_fields})
    if request.method == "POST" and form.is_valid():
        project_services.update_project(project=project, actor=request.user, **form.cleaned_data)
        messages.success(request, "Данные проекта обновлены.")
        return redirect("workspace:project-detail", project_id=project.pk)
    return render(request, "workspace/form.html", context(request, project, "project-detail", form=form, title="Настройки проекта", subtitle="Название, адрес и описание квартиры."))


@login_required
def stage_list(request, project_id):
    project = own_project(request, project_id)
    stages = project.stages.select_related("responsible__user")
    query = request.GET.get("q", "").strip()
    state = request.GET.get("status", "")
    if query:
        stages = stages.filter(name__icontains=query)
    if state:
        stages = stages.filter(status=state)
    from apps.repairs.models import Stage
    return render(request, "workspace/stages.html", context(request, project, "stages", stages=stages, query=query, status_filter=state, stage_statuses=Stage.Status.choices, form=QuickStageForm()))


@login_required
@require_POST
def quick_stage(request, project_id):
    project = own_project(request, project_id)
    check_permission(require_manager, project, request.user)
    form = QuickStageForm(request.POST)
    if form.is_valid():
        try:
            stage_services.create_stage(project=project, actor=request.user, **form.cleaned_data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Этап добавлен. Сроки и ответственного можно указать в его настройках.")
            return redirect("workspace:project-detail", project_id=project.pk)
    return render(request, "workspace/form.html", context(request, project, "stages", form=form, title="Добавить этап", subtitle="Начните с названия работы."))


@login_required
@require_POST
def stage_action(request, project_id, stage_id):
    project = own_project(request, project_id)
    stage = get_object_or_404(project.stages, pk=stage_id)
    try:
        action = request.POST.get("action")
        if action in ["up", "down"]:
            stage_services.move_stage(stage=stage, actor=request.user, direction=action)
        else:
            raise Http404
    except APIException as error:
        messages.error(request, flatten_errors(error.detail))
    return redirect("workspace:project-detail", project_id=project.pk)


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
    return render(request, "workspace/form.html", context(request, project, "stages", form=form, title="Редактирование этапа" if stage else "Новый этап", subtitle="Для плана достаточно названия. Перед началом работ нужны сроки и ответственный из команды проекта.", stage=stage))


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
    initial_role = "foreman" if request.GET.get("role") == "foreman" else "executor"
    form = MemberForm(request.POST or None, owner=request.user, project=project, initial={"role": initial_role})
    if request.method == "POST":
        check_permission(require_customer, project, request.user)
        if form.is_valid():
            try:
                result = project_services.add_member(project=project, actor=request.user,
                    email=form.cleaned_data["email"], contact_id=form.cleaned_data["contact"].pk if form.cleaned_data["contact"] else None,
                    role=form.cleaned_data["role"])
            except APIException as error:
                form_service_error(form, error)
            else:
                if isinstance(result, ProjectMember):
                    message = "Участник добавлен в команду и сохранён в вашем списке."
                elif result.role == "foreman":
                    message = "Приглашение прорабу создано. Он получит доступ после принятия — ссылка ниже, а приглашение также появится в его кабинете."
                else:
                    message = "Контакт сохранён. Аккаунт ещё не подтверждён или не создан — передайте человеку ссылку приглашения ниже."
                messages.success(request, message)
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
            return redirect("workspace:project-detail", project_id=member.project_id)
    available = invitation.status == "pending" and invitation.expires_at > timezone.now()
    return render(request, "workspace/accept.html", context(request, page="projects", invitation=invitation, available=available, error=error))


@login_required
def history(request, project_id):
    project = own_project(request, project_id)
    check_permission(require_manager, project, request.user)
    return render(request, "workspace/history.html", context(request, project, "history", events=project.events.select_related("actor")[:100]))
