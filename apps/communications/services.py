"""Notifications on the site and by email (ТЗ FR19).

* Events: every record in the project history goes through rules.notes_for(); the recipients
  get a notification on the site right away and an email after the transaction is saved.
* Deadlines: check_deadlines() reminds about tasks, remarks and stages that are due soon or
  overdue. It runs once a day on its own (on the first page opened that day) and can be started
  by hand: `manage.py notify_deadlines`.
"""
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMessage, get_connection
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone

from apps.projects.models import ProjectMember
from .models import Notification, NotificationSettings
from .rules import CUSTOMER, EXECUTOR, FOREMAN, Note, members, notes_for, url

logger = logging.getLogger(__name__)

DUE_SOON_DAYS = 2  # the same window as «Скоро срок» on the task list


# ---------- Creating ----------

def save_notes(project, notes, event=None, actor=None):
    """Store notifications and email them once the surrounding transaction is committed."""
    created, seen = [], set()
    for note in notes:
        key = getattr(note, "key", "")
        fields = dict(user=note.user, project=project, event=event, actor=actor, kind=note.kind,
                      title=note.title[:255], text=note.text, url=note.url, key=key)
        if not key:
            created.append(Notification.objects.create(**fields))
            continue
        if (note.user.pk, key) in seen or Notification.objects.filter(user=note.user, key=key).exists():
            continue
        seen.add((note.user.pk, key))
        try:
            with transaction.atomic():  # another process may have created the same reminder a moment ago
                created.append(Notification.objects.create(**fields))
        except IntegrityError:
            pass
    if created:
        ids = [item.pk for item in created]
        transaction.on_commit(lambda: send_emails(ids))
    return created


def on_project_event(sender, instance, created, **kwargs):
    """post_save of ProjectEvent. A failure here must never break the change itself."""
    if not created or kwargs.get("raw"):
        return
    try:
        with transaction.atomic():  # savepoint: an error does not spoil the outer transaction
            notes = notes_for(instance)
            save_notes(instance.project, notes, event=instance, actor=instance.actor)
    except Exception:
        logger.exception("Could not create notifications for event %s (%s)", instance.pk, instance.action)


# ---------- Email ----------

def wants_email(user):
    prefs = NotificationSettings.objects.filter(user=user).first()
    return (prefs is None or prefs.email_enabled) and bool(user.email) and user.is_active


def email_message(notification):
    site = settings.SITE_URL.rstrip("/")
    name = notification.user.get_full_name() or notification.user.email
    lines = [f"Здравствуйте, {name}!", "", notification.title]
    if notification.text:
        lines.append(notification.text)
    lines += ["", f"Проект: {notification.project.name}"]
    if notification.url:
        lines.append(f"Открыть: {site}{notification.url}")
    lines += ["", "—", "Контроль ремонта квартиры",
              f"Все уведомления и настройка писем: {site}/notifications/"]
    return EmailMessage(f"{notification.title} — {notification.project.name}", "\n".join(lines), None, [notification.user.email])


def deliver(ids):
    notifications = [item for item in Notification.objects.filter(pk__in=ids, emailed_at__isnull=True).select_related("user", "project")
                     if wants_email(item.user)]
    if not notifications:
        return 0
    sent = []
    try:
        with get_connection() as connection:  # one SMTP session for the whole batch
            for item in notifications:
                try:
                    connection.send_messages([email_message(item)])
                    sent.append(item.pk)
                except Exception:
                    logger.exception("Could not email notification %s to %s", item.pk, item.user.email)
    except Exception:  # no connection to the mail server: the notifications stay on the site
        logger.exception("Mail server is unavailable, %s notification(s) not emailed", len(notifications))
    Notification.objects.filter(pk__in=sent).update(emailed_at=timezone.now())
    return len(sent)


def send_emails(ids):
    """Send in the background so a slow mail server does not slow down the page."""
    if not settings.NOTIFICATION_EMAIL_ASYNC:
        return deliver(ids)

    def run():
        try:
            deliver(ids)
        finally:
            close_old_connections()
    threading.Thread(target=run, name="notification-email", daemon=True).start()


# ---------- Deadlines ----------

def deadline_notes(today):
    """Reminders keyed by object, date and kind, so each one is sent only once."""
    from apps.quality.models import Remark
    from apps.repairs.models import Stage, Task
    soon = today + timedelta(days=DUE_SOON_DAYS)
    live = {"project__archived_at__isnull": True}
    result = []  # (project, Note with .key)

    def add(project, user, kind, title, text, link, key):
        if user is not None:
            note = Note(user, kind, title, text, link)
            note.key = key
            result.append((project, note))

    tasks = Task.objects.filter(due_date__lte=soon, assignee__is_active=True, **live).exclude(status=Task.Status.DONE) \
        .exclude(stage__status=Stage.Status.CANCELLED).select_related("project", "stage", "assignee__user")
    for task in tasks:
        link, project = url("task", task.project_id, task.pk), task.project
        if task.due_date < today:
            days = (today - task.due_date).days
            text = f"Срок был {task.due_date:%d.%m.%Y}, просрочено на {days} дн. Статус: {task.get_status_display().lower()}."
            add(project, task.assignee.user, "overdue", f"Просрочена задача «{task.name}»", text, link, f"task:{task.pk}:overdue:{task.due_date}")
            for user in members(project, CUSTOMER, FOREMAN):
                add(project, user, "overdue", f"Просрочена задача «{task.name}»",
                    f"Исполнитель: {task.assignee.user.get_full_name() or task.assignee.user.email}. {text}", link, f"task:{task.pk}:overdue:{task.due_date}")
        else:
            when = "сегодня" if task.due_date == today else "завтра" if task.due_date == today + timedelta(days=1) else f"{task.due_date:%d.%m.%Y}"
            add(project, task.assignee.user, "deadline", f"Срок задачи «{task.name}» — {when}",
                f"Выполните задачу до {task.due_date:%d.%m.%Y}. Статус: {task.get_status_display().lower()}.", link, f"task:{task.pk}:soon:{task.due_date}")

    remarks = Remark.objects.filter(status=Remark.Status.OPEN, due_date__lte=soon, responsible__is_active=True, **live) \
        .select_related("project", "responsible__user")
    for remark in remarks:
        link, project = url("remark", remark.project_id, remark.pk), remark.project
        short = remark.description if len(remark.description) <= 60 else remark.description[:57] + "…"
        if remark.due_date < today:
            key = f"remark:{remark.pk}:overdue:{remark.due_date}"
            text = f"Срок устранения был {remark.due_date:%d.%m.%Y}."
            add(project, remark.responsible.user, "overdue", f"Просрочено замечание «{short}»", text, link, key)
            author = project.members.filter(is_active=True, user_id=remark.created_by_id).select_related("user").first()
            if author:
                add(project, author.user, "overdue", f"Просрочено замечание «{short}»", text, link, key)
        else:
            add(project, remark.responsible.user, "deadline", f"Скоро срок устранения замечания «{short}»",
                f"Устраните замечание до {remark.due_date:%d.%m.%Y}.", link, f"remark:{remark.pk}:soon:{remark.due_date}")

    stages = Stage.objects.filter(end_date__lte=soon, status__in=[Stage.Status.IN_PROGRESS, Stage.Status.COMPLETED], **live) \
        .filter(project__status="in_progress").select_related("project")
    for stage in stages:
        link, project = url("stages", stage.project_id), stage.project
        if stage.end_date < today:
            for user in members(project, CUSTOMER, FOREMAN):
                add(project, user, "overdue", f"Этап «{stage.name}» не завершён в срок",
                    f"Окончание этапа было {stage.end_date:%d.%m.%Y}, этап ещё не передан на приёмку.", link, f"stage:{stage.pk}:overdue:{stage.end_date}")
        else:
            for user in members(project, FOREMAN):
                add(project, user, "deadline", f"Скоро окончание этапа «{stage.name}»",
                    f"Этап должен быть завершён до {stage.end_date:%d.%m.%Y}.", link, f"stage:{stage.pk}:soon:{stage.end_date}")
    return result


@transaction.atomic
def check_deadlines(today=None):
    """Create today's reminders. Safe to run many times: a reminder is never repeated."""
    today = today or timezone.localdate()
    by_project, created = {}, []
    for project, note in deadline_notes(today):
        by_project.setdefault(project, []).append(note)
    for project, notes in by_project.items():
        created += save_notes(project, notes)
    return created


def run_daily_check():
    """Called on page loads; the real check happens at most once a day per server process."""
    key = f"notifications:deadlines:{timezone.localdate()}"
    if not settings.NOTIFICATION_DEADLINE_AUTOCHECK or cache.get(key):
        return
    cache.set(key, True, 60 * 60 * 26)
    try:
        check_deadlines()
    except Exception:
        logger.exception("Deadline check failed")


# ---------- Reading ----------

def for_user(user):
    return Notification.objects.filter(user=user, project__members__user=user, project__members__is_active=True).select_related("project", "actor")


def unread_count(user):
    return for_user(user).filter(read_at__isnull=True).count()


def mark_read(notification):
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    return notification


def mark_all_read(user, project=None):
    items = Notification.objects.filter(user=user, read_at__isnull=True)
    if project is not None:
        items = items.filter(project=project)
    return items.update(read_at=timezone.now())


def email_enabled(user):
    prefs = NotificationSettings.objects.filter(user=user).first()
    return prefs is None or prefs.email_enabled


def set_email_enabled(user, enabled):
    NotificationSettings.objects.update_or_create(user=user, defaults={"email_enabled": bool(enabled)})
