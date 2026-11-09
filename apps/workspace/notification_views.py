"""Notifications page (ТЗ FR19): list, open, mark as read, email on/off."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.communications import services
from apps.communications.models import Notification
from .views import CURRENT_PROJECT_KEY, context, own_projects


def current_project(request):
    """The project the user worked with last (as on «Главная»), so the side menu keeps its sections."""
    projects = own_projects(request.user, include_archived=False)
    project_id = request.session.get(CURRENT_PROJECT_KEY)
    return (projects.filter(pk=project_id).first() if project_id else None) or projects.first()


@login_required
def notification_list(request):
    items = services.for_user(request.user)
    projects = own_projects(request.user)
    show, kind, project_id = request.GET.get("show", ""), request.GET.get("kind", ""), request.GET.get("project", "")
    unread_total = items.filter(read_at__isnull=True).count()
    if project_id.isdigit():
        items = items.filter(project_id=project_id)
    if kind in Notification.Kind.values:
        items = items.filter(kind=kind)
    if show == "unread":
        items = items.filter(read_at__isnull=True)
    page = Paginator(items, 30).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "workspace/notifications.html", context(
        request, current_project(request), "notifications", page_obj=page, notifications=page.object_list,
        unread_total=unread_total, show=show, kind=kind, project_filter=project_id, kinds=Notification.Kind.choices,
        filter_projects=projects, email_enabled=services.email_enabled(request.user), query_string=query.urlencode()))


@login_required
def notification_open(request, notification_id):
    notification = get_object_or_404(services.for_user(request.user), pk=notification_id)
    services.mark_read(notification)
    return redirect(notification.url or reverse("workspace:notifications"))


def back(request):
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return redirect(target)
    return redirect("workspace:notifications")


@login_required
@require_POST
def notification_read_all(request):
    count = services.mark_all_read(request.user)
    messages.success(request, f"Отмечено прочитанными: {count}." if count else "Непрочитанных уведомлений нет.")
    return back(request)


@login_required
@require_POST
def notification_settings(request):
    enabled = request.POST.get("email_enabled") == "1"
    services.set_email_enabled(request.user, enabled)
    messages.success(request, "Уведомления будут приходить и на Email." if enabled else "Письма отключены: уведомления остаются на сайте.")
    return back(request)
