from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from apps.projects import services as project_services
from apps.projects.emails import send_invitation_email
from apps.projects.models import Project, ProjectInvitation, ProjectMember
from apps.projects.permissions import active_membership, require_manager
from apps.repairs import services as stage_services
from apps.repairs import task_services
from apps.repairs.models import Stage
from .forms import InvitationForm, ProjectForm, StageForm

CURRENT_PROJECT_KEY = "current_project_id"


def own_projects(user, include_archived=True):
    """Projects where the user is a current member, newest first, with counters for the cards."""
    projects = Project.objects.filter(pk__in=ProjectMember.objects.filter(user=user, is_active=True).values("project_id")).annotate(
        stage_count=Count("stages", distinct=True),
        member_count=Count("members", filter=Q(members__is_active=True), distinct=True),
        task_count=Count("tasks", distinct=True),
    ).order_by("-created_at", "-id")
    return projects if include_archived else projects.filter(archived_at__isnull=True)


def own_project(request, project_id):
    project = get_object_or_404(own_projects(request.user), pk=project_id)
    # Remember the project the user is working with, so "Главная" opens it next time.
    request.session[CURRENT_PROJECT_KEY] = project.pk
    return project


def context(request, project=None, page="dashboard", **extra):
    membership = active_membership(project, request.user) if project else None
    role = membership.role if membership else None
    archived = bool(project and project.is_archived)
    return {
        "projects": own_projects(request.user, include_archived=False), "project": project, "membership": membership,
        # Action buttons disappear in an archived project; navigation still follows the role.
        "is_customer": role == "customer" and not archived,
        "is_manager": role in ("customer", "foreman") and not archived,
        "nav_manager": role in ("customer", "foreman"),
        "is_archived": archived,
        "incoming_count": project_services.pending_invitations_for(request.user).count(),
        "page": page, "today": timezone.localdate(), **extra,
    }


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
        raise PermissionDenied("Недостаточно прав для этого действия.")


def run_service(request, call, success):
    """Run a service call and turn its outcome into a flash message. Returns the result or None on error."""
    try:
        result = call()
    except APIException as error:
        messages.error(request, flatten_errors(error.detail))
        return None
    if success:
        messages.success(request, success)
    return result if result is not None else True


def attention_items(project, user):
    """What is waiting for this person right now: (icon, tone, title, hint, url)."""
    from apps.estimates import services as estimate_services
    from apps.quality.models import Remark
    member = active_membership(project, user)
    if member is None or project.is_archived:
        return []
    url = lambda name, *args: reverse(f"workspace:{name}", args=[project.pk, *args])
    items = []
    open_estimate = estimate_services.open_estimate(project)
    if member.role == "customer":
        if open_estimate and open_estimate.status == "pending":
            items.append(("money", "orange", f"Смета на {open_estimate.total:,.0f} сом ждёт вашего решения".replace(",", " "), "Утвердите или отклоните с причиной", url("estimate")))
        if project.status == "planning" and estimate_services.approved_estimate(project) and not estimate_services.start_blockers(project):
            items.append(("play", "green", "Можно запускать работы", "Смета утверждена, прораб в команде", url("estimate")))
        pending = project.stages.filter(status="pending_acceptance").count()
        if pending:
            items.append(("check", "orange", f"Этапов на приёмке: {pending}", "Примите или верните на доработку", url("acceptance")))
    if member.role in ("customer", "foreman"):
        to_check = Remark.objects.filter(project=project, status="fixed")
        if member.role == "foreman":
            to_check = to_check.filter(created_by=user)
        if to_check.exists():
            items.append(("flag", "blue", f"Замечаний ждут проверки: {to_check.count()}", "Исполнитель сообщил об устранении", url("remarks") + "?status=fixed"))
    if member.role == "foreman":
        latest = estimate_services.latest_estimate(project)
        if latest and latest.status == "rejected" and not open_estimate:
            items.append(("money", "red", "Заказчик отклонил смету", f"«{latest.decision_reason[:80]}» — создайте новую версию", url("estimate")))
        elif open_estimate and open_estimate.status == "draft" and open_estimate.lines.exists():
            items.append(("send", "blue", "Черновик сметы не отправлен", "Проверьте строки и отправьте заказчику", url("estimate")))
        review = project.tasks.filter(status="review").count()
        if review:
            items.append(("tasks", "orange", f"Задач ждут проверки: {review}", "Подтвердите выполнение или верните", url("tasks") + "?status=review"))
    if member.role == "executor":
        todo = project.tasks.filter(assignee=member).exclude(status__in=["done", "review"]).count()
        if todo:
            items.append(("tasks", "blue", f"Ваших задач в работе и к выполнению: {todo}", "Откройте задачу, чтобы начать или сдать", url("tasks") + "?deadline=open"))
        remarks = Remark.objects.filter(project=project, responsible=member, status="open").count()
        if remarks:
            items.append(("flag", "red", f"Замечаний на устранение: {remarks}", "Устраните и нажмите «Устранено»", url("remarks") + "?status=open"))
    return items


@login_required
def dashboard(request):
    projects = own_projects(request.user, include_archived=False)
    project_id = request.GET.get("project") or request.session.get(CURRENT_PROJECT_KEY)
    project = projects.filter(pk=project_id).first() if str(project_id or "").isdigit() else None
    project = project or projects.first()
    if project:
        request.session[CURRENT_PROJECT_KEY] = project.pk
    stages = list(project.stages.select_related("responsible__user")) if project else []
    progress = task_services.project_progress(project) if project else None
    open_tasks = task_services.visible_tasks(project, request.user).exclude(status="done") if project else []
    return render(request, "workspace/dashboard.html", context(
        request, project, stages=stages[:5], total_stages=len(stages), progress=progress, upcoming=list(open_tasks[:5]),
        incoming=project_services.pending_invitations_for(request.user), attention=attention_items(project, request.user) if project else [],
        open_task_count=open_tasks.count() if project else 0,
        my_open_tasks=open_tasks.filter(assignee__user=request.user).count() if project else 0,
        # Counters follow task visibility: an executor counts only their own tasks.
        review_count=open_tasks.filter(status="review").count() if project else 0,
        overdue_count=open_tasks.filter(due_date__lt=timezone.localdate()).count() if project else 0))


@login_required
def project_list(request):
    show_archived = request.GET.get("archived") == "1"
    projects = own_projects(request.user)
    projects = projects.filter(archived_at__isnull=False) if show_archived else projects.filter(archived_at__isnull=True)
    query = request.GET.get("q", "").strip()
    if query:
        projects = projects.filter(Q(name__icontains=query) | Q(address__icontains=query))
    memberships = {m.project_id: m.get_role_display() for m in ProjectMember.objects.filter(user=request.user, is_active=True)}
    items = [{"project": p, "role": memberships.get(p.pk, "")} for p in projects]
    archived_count = own_projects(request.user).filter(archived_at__isnull=False).count()
    return render(request, "workspace/projects.html", context(
        request, page="projects", items=items, query=query, show_archived=show_archived, archived_count=archived_count,
        incoming=project_services.pending_invitations_for(request.user)))


@login_required
def project_create(request):
    form = ProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            project = project_services.create_project(user=request.user, **form.cleaned_data)
        except APIException as error:
            form_service_error(form, error)
        else:
            request.session[CURRENT_PROJECT_KEY] = project.pk
            messages.success(request, f"Проект «{project.name}» создан. Дальше — пригласите прораба и добавьте этапы.")
            return redirect("workspace:project", project_id=project.pk)
    return render(request, "workspace/form.html", context(request, page="projects", form=form, title="Новый проект",
                  subtitle="Название обязательно, остальные поля можно заполнить позже.", section="проекта"))


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
    return render(request, "workspace/stages.html", context(request, project, "stages", stages=stages, query=query,
                  status_filter=state, stage_statuses=Stage.Status.choices))


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
    return render(request, "workspace/form.html", context(request, project, "stages", form=form, title="Редактирование этапа" if stage else "Новый этап",
                  subtitle="Укажите сроки работ и ответственного из команды проекта.", stage=stage, section="этапа", cancel_url="workspace:stages"))


@login_required
@require_POST
def stage_delete(request, project_id, stage_id):
    project = own_project(request, project_id)
    stage = get_object_or_404(project.stages, pk=stage_id)
    run_service(request, lambda: stage_services.delete_stage(stage=stage, actor=request.user), "Черновик этапа удалён.")
    return redirect("workspace:stages", project_id=project.pk)


def invitation_link(request, invitation):
    return request.build_absolute_uri(reverse("workspace:accept", args=[invitation.token]))


@login_required
def members(request, project_id):
    project = own_project(request, project_id)
    membership = active_membership(project, request.user)
    can_invite = membership.role in ("customer", "foreman") and not project.is_archived
    form = InvitationForm(request.POST or None, inviter_role=membership.role)
    if request.method == "POST":
        if not can_invite:
            raise PermissionDenied("Недостаточно прав для этого действия.")
        if form.is_valid():
            try:
                invitation = project_services.invite_member(project=project, actor=request.user, **form.cleaned_data)
            except APIException as error:
                form_service_error(form, error)
            else:
                if send_invitation_email(invitation, invitation_link(request, invitation)):
                    messages.success(request, f"Приглашение отправлено на {invitation.email}. После входа под этим email человек увидит его на главной странице.")
                else:
                    messages.warning(request, f"Приглашение создано, но письмо на {invitation.email} отправить не удалось. Скопируйте ссылку ниже и передайте её вручную.")
                return redirect("workspace:members", project_id=project.pk)
    invitations = []
    if membership.role in ("customer", "foreman"):
        invitations = list(project.invitations.select_related("invited_by"))
        if membership.role == "foreman":
            invitations = [item for item in invitations if item.role == "executor"]
        for invitation in invitations:
            invitation.accept_url = invitation_link(request, invitation)
            invitation.expired = invitation.status == "pending" and invitation.expires_at <= timezone.now()
            invitation.can_cancel = invitation.status == "pending" and not invitation.expired and not project.is_archived and (
                membership.role == "customer" or invitation.invited_by_id == request.user.pk)
    team = list(project.members.filter(is_active=True).select_related("user"))
    for member in team:
        member.work = project_services.unfinished_work(member) if member.role != "customer" else None
        member.work_text = project_services.describe_work(member.work) if member.work else ""
        member.replacements = [other for other in team if other.pk != member.pk]
    former = project.members.filter(is_active=False).select_related("user", "removed_by")
    return render(request, "workspace/members.html", context(request, project, "members", team=team, former=former,
                  invitations=invitations, form=form, can_invite=can_invite))


@login_required
@require_POST
def member_action(request, project_id, member_id):
    project = own_project(request, project_id)
    action = request.POST.get("action")
    if action == "remove":
        run_service(request, lambda: project_services.remove_member(project=project, actor=request.user, member_id=member_id,
                    replacement_id=request.POST.get("replacement") or None), "Участник удалён из проекта. Его выполненная работа и история сохранены.")
    elif action == "role":
        run_service(request, lambda: project_services.change_member_role(project=project, actor=request.user, member_id=member_id,
                    role=request.POST.get("role")), "Роль участника изменена.")
    else:
        raise Http404
    return redirect("workspace:members", project_id=project.pk)


@login_required
@require_POST
def invitation_cancel(request, project_id, invitation_id):
    project = own_project(request, project_id)
    run_service(request, lambda: project_services.cancel_invitation(project=project, actor=request.user, invitation_id=invitation_id), "Приглашение отменено.")
    return redirect("workspace:members", project_id=project.pk)


@login_required
def accept(request, token):
    """Invitation page. Explains what to do when the link is opened under another account instead of a bare 404."""
    invitation = get_object_or_404(ProjectInvitation.objects.select_related("project", "invited_by"), token=token)
    own_email = (request.user.email or "").strip().lower() == invitation.email.lower()
    error = None
    if request.method == "POST" and own_email:
        decision = request.POST.get("decision", "accept")
        try:
            if decision == "decline":
                project_services.decline_invitation(token=token, user=request.user)
            else:
                member = project_services.accept_invitation(token=token, user=request.user)
        except APIException as exc:
            error = flatten_errors(exc.detail)
        else:
            if decision == "decline":
                messages.success(request, "Приглашение отклонено.")
                return redirect("workspace:dashboard")
            request.session[CURRENT_PROJECT_KEY] = member.project_id
            messages.success(request, f"Вы присоединились к проекту «{invitation.project.name}».")
            return redirect("workspace:project", project_id=member.project_id)
    expired = invitation.expires_at <= timezone.now()
    available = invitation.status == "pending" and not expired and not invitation.project.is_archived
    return render(request, "workspace/accept.html", context(request, page="projects", invitation=invitation, available=available,
                  own_email=own_email, expired=expired, error=error,
                  relogin_url=reverse("accounts:login") + "?next=" + reverse("workspace:accept", args=[token])))


@login_required
def history(request, project_id):
    project = own_project(request, project_id)
    check_permission(require_manager, project, request.user)
    return render(request, "workspace/history.html", context(request, project, "history", events=project.events.select_related("actor")[:100]))
