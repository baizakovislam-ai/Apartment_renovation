"""FR19: notifications on the site and by email."""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.estimates import services as estimate_services
from apps.estimates.testing import mark_started
from apps.finances import services as expense_services
from apps.projects import services as project_services
from apps.projects.models import ProjectMember
from apps.quality import services as quality_services
from apps.repairs import task_services
from apps.repairs.models import Stage, Task
from apps.repairs.services import create_stage
from . import services
from .models import Notification
from .rules import money


@override_settings(NOTIFICATION_EMAIL_ASYNC=False, NOTIFICATION_DEADLINE_AUTOCHECK=False, SITE_URL="http://testserver",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class NotificationTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        make = lambda email: User.objects.create_user(email=email, password="RepairDemo2026!", full_name=email.split("@")[0], is_email_verified=True)
        self.customer, self.foreman, self.executor, self.executor2, self.stranger = map(make, [
            "customer@example.com", "foreman@example.com", "executor@example.com", "executor2@example.com", "stranger@example.com"])
        self.project = project_services.create_project(user=self.customer, name="Ремонт квартиры")
        mark_started(self.project)
        self.foreman_member = ProjectMember.objects.create(project=self.project, user=self.foreman, role="foreman")
        self.executor_member = ProjectMember.objects.create(project=self.project, user=self.executor, role="executor")
        self.executor2_member = ProjectMember.objects.create(project=self.project, user=self.executor2, role="executor")
        self.today = timezone.localdate()
        self.stage = create_stage(project=self.project, actor=self.customer, name="Электрика", start_date=self.today - timedelta(days=10),
                                  end_date=self.today + timedelta(days=20), responsible=self.foreman_member)
        Stage.objects.filter(pk=self.stage.pk).update(status=Stage.Status.IN_PROGRESS)
        self.stage.refresh_from_db()
        Notification.objects.all().delete()

    def run_saved(self, call):
        """Run a service and the after-commit callbacks (emails), as in a real request."""
        with self.captureOnCommitCallbacks(execute=True):
            return call()

    def create_task(self, assignee=None, due=3, name="Кабель на кухне"):
        return self.run_saved(lambda: task_services.create_task(
            project=self.project, actor=self.foreman, stage=self.stage, name=name,
            assignee=assignee or self.executor_member, due_date=self.today + timedelta(days=due)))

    def notes(self, user):
        return list(Notification.objects.filter(user=user))


class EventNotificationTests(NotificationTestCase):
    def test_task_assignment_notifies_executor_on_site_and_by_email(self):
        task = self.create_task()
        notes = self.notes(self.executor)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].kind, "task")
        self.assertIn("Вам назначена задача «Кабель на кухне»", notes[0].title)
        self.assertEqual(notes[0].url, reverse("workspace:task", args=[self.project.pk, task.pk]))
        self.assertEqual(self.notes(self.foreman), [], "the author is not notified about their own action")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["executor@example.com"])
        self.assertIn("http://testserver/projects/", mail.outbox[0].body)
        notes[0].refresh_from_db()
        self.assertIsNotNone(notes[0].emailed_at)

    def test_task_changes_and_reassignment(self):
        task = self.create_task()
        task = self.run_saved(lambda: task_services.update_task(task=task, actor=self.foreman, version=task.version,
                                                                due_date=self.today + timedelta(days=5)))
        self.assertIn("Изменена задача", self.notes(self.executor)[0].title)
        self.assertIn("срок", self.notes(self.executor)[0].text)
        self.run_saved(lambda: task_services.update_task(task=task, actor=self.foreman, version=task.version, assignee=self.executor2_member))
        self.assertIn("передана другому исполнителю", self.notes(self.executor)[0].title)
        self.assertIn("Вам назначена задача", self.notes(self.executor2)[0].title)

    def test_task_review_flow(self):
        task = self.create_task()
        task = self.run_saved(lambda: task_services.task_action(task=task, actor=self.executor, action="start", version=task.version))
        task = self.run_saved(lambda: task_services.task_action(task=task, actor=self.executor, action="submit", version=task.version, text="Готово"))
        for user in (self.customer, self.foreman):
            self.assertEqual(self.notes(user)[0].kind, "approval")
            self.assertIn("ждёт проверки", self.notes(user)[0].title)
        task = self.run_saved(lambda: task_services.task_action(task=task, actor=self.foreman, action="return", version=task.version, text="Переделать розетку"))
        self.assertIn("возвращена на доработку", self.notes(self.executor)[0].title)
        self.assertIn("Переделать розетку", self.notes(self.executor)[0].text)

    def test_deleted_task_tells_the_executor(self):
        task = self.create_task()
        self.run_saved(lambda: task_services.delete_task(task=task, actor=self.foreman, version=task.version))
        self.assertIn("удалена", self.notes(self.executor)[0].title)

    def test_remark_flow(self):
        remark = self.run_saved(lambda: quality_services.create_remark(
            project=self.project, actor=self.customer, stage=self.stage, description="Кривая розетка",
            responsible=self.executor_member, due_date=self.today + timedelta(days=2)))
        self.assertIn("Новое замечание", self.notes(self.executor)[0].title)
        self.assertIn("Замечание исполнителю", self.notes(self.foreman)[0].title)
        remark = self.run_saved(lambda: quality_services.remark_action(remark=remark, actor=self.executor, action="fix", version=remark.version))
        self.assertIn("Замечание устранено", self.notes(self.customer)[0].title)
        self.run_saved(lambda: quality_services.remark_action(remark=remark, actor=self.customer, action="close", version=remark.version))
        self.assertIn("Замечание закрыто", self.notes(self.executor)[0].title)

    def test_estimate_approval_flow(self):
        estimate = estimate_services.create_draft(project=self.project, actor=self.foreman)
        estimate_services.add_line(estimate=estimate, actor=self.foreman, revision=estimate.revision, stage=self.stage,
                                   name="Кабель", unit="м", quantity="100", price="3200")
        estimate.refresh_from_db()
        estimate = self.run_saved(lambda: estimate_services.submit_estimate(estimate=estimate, actor=self.foreman))
        note = self.notes(self.customer)[0]
        self.assertIn("ждёт согласования", note.title)
        self.assertIn("320 000 сом", note.text)
        self.assertEqual(self.notes(self.executor), [], "money is not shown to executors")
        self.run_saved(lambda: estimate_services.reject_estimate(estimate=estimate, actor=self.customer, reason="Дорого"))
        self.assertIn("отклонена", self.notes(self.foreman)[0].title)
        self.assertIn("Дорого", self.notes(self.foreman)[0].text)

    def test_expense_confirmation_flow(self):
        expense = self.run_saved(lambda: expense_services.create_expense(
            project=self.project, actor=self.foreman, submit=True, amount="15000", date=self.today, recipient="Магазин", purpose="Кабель"))
        self.assertIn("ждёт подтверждения", self.notes(self.customer)[0].title)
        expense.refresh_from_db()
        self.run_saved(lambda: expense_services.confirm_expense(expense=expense, actor=self.customer, revision=expense.revision))
        self.assertIn("подтверждён", self.notes(self.foreman)[0].title)

    def test_email_can_be_turned_off(self):
        services.set_email_enabled(self.executor, False)
        self.create_task()
        self.assertEqual(len(self.notes(self.executor)), 1)
        self.assertEqual(mail.outbox, [])

    def test_mail_server_failure_does_not_break_the_change(self):
        with mock.patch("django.core.mail.backends.locmem.EmailBackend.send_messages", side_effect=OSError("SMTP down")):
            task = self.create_task()
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())
        self.assertIsNone(self.notes(self.executor)[0].emailed_at)

    def test_rule_error_does_not_break_the_change(self):
        with mock.patch("apps.communications.services.notes_for", side_effect=RuntimeError("bug")):
            task = self.create_task()
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())
        self.assertEqual(Notification.objects.count(), 0)

    def test_money_format(self):
        self.assertEqual(money("320000.00"), "320 000 сом")
        self.assertEqual(money("1500.5"), "1 500,50 сом")
        self.assertEqual(money("-2000"), "−2 000 сом")


class DeadlineTests(NotificationTestCase):
    def make_task(self, due, status=Task.Status.TODO, name="Штробы"):
        return Task.objects.create(project=self.project, stage=self.stage, name=name, assignee=self.executor_member,
                                   due_date=self.today + timedelta(days=due), status=status, created_by=self.foreman)

    def test_due_soon_and_overdue_tasks(self):
        soon = self.make_task(1, name="Скоро")
        late = self.make_task(-2, name="Опоздали")
        self.make_task(-2, status=Task.Status.DONE, name="Сделано")
        self.make_task(10, name="Не скоро")
        with self.captureOnCommitCallbacks(execute=True):
            services.check_deadlines()
        executor = {note.title: note for note in self.notes(self.executor)}
        self.assertEqual(set(executor), {"Срок задачи «Скоро» — завтра", "Просрочена задача «Опоздали»"})
        self.assertEqual(executor["Просрочена задача «Опоздали»"].kind, "overdue")
        self.assertIn("просрочено на 2 дн.", executor["Просрочена задача «Опоздали»"].text)
        for user in (self.customer, self.foreman):
            self.assertEqual([n.title for n in self.notes(user)], ["Просрочена задача «Опоздали»"])
        self.assertTrue(any(message.to == ["executor@example.com"] for message in mail.outbox))
        self.assertTrue(soon and late)

    def test_reminders_are_not_repeated(self):
        self.make_task(-1)
        first = services.check_deadlines()
        second = services.check_deadlines()
        self.assertEqual(len(first), 3)
        self.assertEqual(second, [])

    def test_moved_deadline_gets_a_new_reminder(self):
        task = self.make_task(1)
        services.check_deadlines()
        Task.objects.filter(pk=task.pk).update(due_date=self.today)
        created = services.check_deadlines()
        self.assertEqual([note.title for note in created], ["Срок задачи «Штробы» — сегодня"])

    def test_remarks_and_stages(self):
        remark = quality_services.create_remark(project=self.project, actor=self.customer, stage=self.stage, description="Скол плитки",
                                                responsible=self.executor_member, due_date=self.today)
        Stage.objects.filter(pk=self.stage.pk).update(end_date=self.today - timedelta(days=1))
        Notification.objects.all().delete()
        services.check_deadlines()
        self.assertIn("Скоро срок устранения замечания «Скол плитки»", [n.title for n in self.notes(self.executor)])
        self.assertIn("Этап «Электрика» не завершён в срок", [n.title for n in self.notes(self.customer)])
        self.assertTrue(remark)

    def test_archived_project_is_quiet(self):
        self.make_task(-1)
        project_services.archive_project(project=self.project, actor=self.customer, reason="Пауза")
        Notification.objects.all().delete()
        self.assertEqual(services.check_deadlines(), [])

    def test_command(self):
        self.make_task(-1)
        out = StringIO()
        call_command("notify_deadlines", stdout=out)
        self.assertIn("Создано напоминаний: 3", out.getvalue())

    @override_settings(NOTIFICATION_DEADLINE_AUTOCHECK=True)
    def test_check_runs_by_itself_on_a_page_load(self):
        from django.core.cache import cache
        cache.clear()
        self.make_task(-1)
        self.client.force_login(self.executor)
        self.client.get(reverse("workspace:dashboard"))
        self.assertEqual(len(self.notes(self.executor)), 1)


class NotificationPageTests(NotificationTestCase):
    def setUp(self):
        super().setUp()
        self.task = self.create_task()
        self.note = self.notes(self.executor)[0]

    def test_bell_and_list(self):
        self.client.force_login(self.executor)
        response = self.client.get(reverse("workspace:notifications"))
        self.assertContains(response, "Вам назначена задача «Кабель на кухне»")
        self.assertContains(response, 'class="icon-button header-bell"')
        self.assertContains(response, "Непрочитанные <b>1</b>", html=False)
        response = self.client.get(reverse("workspace:notifications") + "?kind=approval")
        self.assertNotContains(response, "Кабель на кухне")

    def test_open_marks_read_and_redirects(self):
        self.client.force_login(self.executor)
        response = self.client.get(reverse("workspace:notification-open", args=[self.note.pk]))
        self.assertRedirects(response, self.note.url)
        self.note.refresh_from_db()
        self.assertIsNotNone(self.note.read_at)

    def test_other_users_notification_is_hidden(self):
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse("workspace:notification-open", args=[self.note.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("workspace:notifications")), "Кабель на кухне")

    def test_read_all_and_email_setting(self):
        self.client.force_login(self.executor)
        self.client.post(reverse("workspace:notifications-read-all"))
        self.assertEqual(services.unread_count(self.executor), 0)
        self.client.post(reverse("workspace:notification-settings"), {"email_enabled": "0"})
        self.assertFalse(services.email_enabled(self.executor))
        self.assertContains(self.client.get(reverse("workspace:notifications")), "Включить письма")

    def test_removed_member_no_longer_sees_project_notifications(self):
        self.assertEqual(services.unread_count(self.executor), 1)
        ProjectMember.objects.filter(pk=self.executor_member.pk).update(is_active=False)
        self.assertEqual(services.unread_count(self.executor), 0)
        self.client.force_login(self.executor)
        self.assertEqual(self.client.get(reverse("workspace:notification-open", args=[self.note.pk])).status_code, 404)

    def test_api(self):
        self.client.force_login(self.executor)
        data = self.client.get("/api/notifications/").json()
        self.assertEqual(data["unread"], 1)
        self.assertEqual(data["results"][0]["title"], self.note.title)
        self.assertTrue(self.client.post(f"/api/notifications/{self.note.pk}/read/").json()["is_read"])
        self.assertEqual(self.client.get("/api/notifications/?unread=1").json()["results"], [])
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.post(f"/api/notifications/{self.note.pk}/read/").status_code, 404)
