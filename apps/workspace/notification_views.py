"""Notifications page (ТЗ FR19): list, open, mark as read, email on/off."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
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


PANEL_SIZE = 8


def day_groups(items):
    """[(«Сегодня», [...]), («Вчера», [...]), («12 ноября 2026», [...])] for the page."""
    today = timezone.localdate()
    groups = []
    for item in items:
        day = timezone.localtime(item.created_at).date()
        label = "Сегодня" if day == today else "Вчера" if (today - day).days == 1 else date_format(day, "j E Y")
        if not groups or groups[-1][0] != label:
            groups.append((label, []))
        groups[-1][1].append(item)
    return groups


@login_required
def notification_list(request):
    items = services.for_user(request.user)
    projects = own_projects(request.user)
    show, kind, project_id = request.GET.get("show", ""), request.GET.get("kind", ""), request.GET.get("project", "")
    unread_total = items.filter(read_at__isnull=True).count()
    all_items = items
    if project_id.isdigit():
        items = items.filter(project_id=project_id)
    kind_counts = dict(items.order_by().values("kind").annotate(total=Count("id")).values_list("kind", "total"))
    if kind in Notification.Kind.values:
        items = items.filter(kind=kind)
    if show == "unread":
        items = items.filter(read_at__isnull=True)
    page = Paginator(items, 30).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    kind_query = request.GET.copy()
    for name in ("page", "kind"):
        kind_query.pop(name, None)
    return render(request, "workspace/notifications.html", context(
        request, current_project(request), "notifications", page_obj=page, notifications=page.object_list,
        groups=day_groups(page.object_list), kind_query=kind_query.urlencode(),
        unread_total=unread_total, show=show, kind=kind, project_filter=project_id, kinds=[(value, label, kind_counts.get(value, 0)) for value, label in Notification.Kind.choices],
        total=all_items.count(), week_total=all_items.filter(created_at__gte=timezone.now() - timedelta(days=7)).count(),
        filter_projects=projects, email_enabled=services.email_enabled(request.user), query_string=query.urlencode()))


@login_required
def notification_open(request, notification_id):
    notification = get_object_or_404(services.for_user(request.user), pk=notification_id)
    services.mark_read(notification)
    return redirect(notification.url or reverse("workspace:notifications"))


@login_required
def notification_panel(request):
    """The drop-down list under the bell in the header; loaded by app.js when the bell is clicked."""
    items = services.for_user(request.user)
    return render(request, "workspace/_notification_panel.html", {
        "notifications": items[:PANEL_SIZE], "unread_total": items.filter(read_at__isnull=True).count(),
        "next": request.GET.get("next", ""),
    })


@login_required
def notification_count(request):
    """Polled by app.js once a minute, so the red number on the bell appears without reloading the page."""
    services.run_daily_check()
    return JsonResponse({"unread": services.unread_count(request.user)})


@login_required
@require_POST
def notification_read(request, notification_id):
    services.mark_read(get_object_or_404(services.for_user(request.user), pk=notification_id))
    return back(request)


def back(request):
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return redirect(target)
    return redirect("workspace:notifications")


@login_required
@require_POST
def notification_read_all(request):
    count = services.mark_all_read(request.user)
    if request.headers.get("X-Requested-With") == "fetch":  # «Прочитать все» in the bell list
        return JsonResponse({"read": count, "unread": services.unread_count(request.user)})
    messages.success(request, f"Отмечено прочитанными: {count}." if count else "Непрочитанных уведомлений нет.")
    return back(request)


@login_required
@require_POST
def notification_settings(request):
    enabled = request.POST.get("email_enabled") == "1"
    services.set_email_enabled(request.user, enabled)
    messages.success(request, "Уведомления будут приходить и на Email." if enabled else "Письма отключены: уведомления остаются на сайте.")
    return back(request)
