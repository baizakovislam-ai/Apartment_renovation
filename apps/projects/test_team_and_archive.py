"""Team changes and project removal — the scenarios that blocked real use."""
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.estimates.testing import mark_started
from apps.projects.models import Project, ProjectInvitation, ProjectMember
from apps.projects.services import (accept_invitation, archive_project, create_project, invite_member, remove_member,
                                    restore_project)
from apps.repairs.models import Task
from apps.repairs.services import create_stage, update_stage
from apps.repairs.task_services import create_task, task_action

TEMP_MEDIA = tempfile.mkdtemp()


@override_settings(PRIVATE_MEDIA_ROOT=TEMP_MEDIA, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TeamAndArchiveTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        User = get_user_model()
        make = lambda email: User.objects.create_user(email=email, full_name=email.split("@")[0], is_email_verified=True)
        self.customer, self.foreman, self.worker, self.worker2, self.other_customer = map(make, [
            "customer@example.com", "foreman@example.com", "worker@example.com", "worker2@example.com", "other@example.com"])
        self.project = create_project(user=self.customer, name="Квартира")
        mark_started(self.project)  # works started: estimate approved (ТЗ 4.2)
        self.foreman_member = self.join(self.project, self.customer, self.foreman, "foreman")
        self.worker_member = self.join(self.project, self.customer, self.worker, "executor")
        self.worker2_member = self.join(self.project, self.customer, self.worker2, "executor")
        self.today = timezone.localdate()
        self.stage = create_stage(project=self.project, actor=self.foreman, name="Электрика", start_date=self.today,
                                  end_date=self.today + timedelta(days=7), responsible=self.foreman_member)
        update_stage(stage=self.stage, actor=self.foreman, status="in_progress")

    def join(self, project, inviter, user, role):
        invitation = invite_member(project=project, actor=inviter, email=user.email, role=role)
        return accept_invitation(token=invitation.token, user=user)

    def task(self, assignee, name="Розетки"):
        return create_task(project=self.project, actor=self.foreman, stage=self.stage, name=name, assignee=assignee, due_date=self.today)

    def finish(self, task):
        for action, actor, text in [("start", task.assignee.user, ""), ("submit", task.assignee.user, "Готово"), ("approve", self.foreman, "")]:
            task.refresh_from_db()
            task_action(task=task, actor=actor, action=action, version=task.version, text=text)

    # --- removing members ---

    def test_member_who_finished_work_can_be_removed_and_work_stays(self):
        task = self.task(self.worker_member)
        self.finish(task)
        remove_member(project=self.project, actor=self.customer, member_id=self.worker_member.pk)
        self.worker_member.refresh_from_db()
        task.refresh_from_db()
        self.assertFalse(self.worker_member.is_active)
        self.assertEqual(task.assignee, self.worker_member)  # finished work is still attributed
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse("workspace:tasks", args=[self.project.pk])).status_code, 404)
        self.assertEqual(self.client.get(f"/api/tasks/{task.pk}/").status_code, 404)

    def test_unfinished_work_is_handed_over_on_removal(self):
        task = self.task(self.worker_member)
        task_action(task=task, actor=self.worker, action="start", version=task.version)
        with self.assertRaises(ValidationError):
            remove_member(project=self.project, actor=self.customer, member_id=self.worker_member.pk)
        with self.assertRaises(ValidationError):  # tasks go to executors only
            remove_member(project=self.project, actor=self.customer, member_id=self.worker_member.pk, replacement_id=self.foreman_member.pk)
        remove_member(project=self.project, actor=self.customer, member_id=self.worker_member.pk, replacement_id=self.worker2_member.pk)
        task.refresh_from_db()
        self.assertEqual((task.assignee, task.status), (self.worker2_member, "todo"))

    def test_foreman_responsible_for_accepted_stage_can_be_removed(self):
        """The real dead end: the foreman owned an accepted stage, which can no longer be edited."""
        task = self.task(self.worker_member)
        self.finish(task)
        from apps.quality.services import accept_stage, submit_stage
        submit_stage(stage=self.stage, actor=self.foreman)
        accept_stage(stage=self.stage, actor=self.customer)
        remove_member(project=self.project, actor=self.customer, member_id=self.foreman_member.pk)
        self.foreman_member.refresh_from_db()
        self.assertFalse(self.foreman_member.is_active)

    def test_removed_member_can_be_invited_again_and_to_other_projects(self):
        task = self.task(self.worker_member)
        self.finish(task)
        remove_member(project=self.project, actor=self.customer, member_id=self.worker_member.pk)
        member = self.join(self.project, self.customer, self.worker, "executor")
        self.assertEqual(member.pk, self.worker_member.pk)  # same row, earlier work stays linked
        self.assertTrue(member.is_active)
        other = create_project(user=self.other_customer, name="Другая квартира")
        in_other = self.join(other, self.other_customer, self.worker, "foreman")
        self.assertEqual(in_other.role, "foreman")

    def test_new_foreman_after_removing_the_old_one(self):
        remove_member(project=self.project, actor=self.customer, member_id=self.foreman_member.pk, replacement_id=self.worker_member.pk)
        new_foreman = get_user_model().objects.create_user(email="new@example.com", full_name="Новый", is_email_verified=True)
        self.assertEqual(self.join(self.project, self.customer, new_foreman, "foreman").role, "foreman")

    # --- invitations ---

    def test_foreman_invites_executors_and_email_is_sent(self):
        self.client.force_login(self.foreman)
        url = reverse("workspace:members", args=[self.project.pk])
        response = self.client.post(url, {"email": "brigade@example.com", "role": "executor"}, follow=True)
        self.assertContains(response, "Приглашение отправлено на brigade@example.com")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/invitations/", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["brigade@example.com"])
        self.client.post(url, {"email": "boss@example.com", "role": "foreman"})
        self.assertFalse(ProjectInvitation.objects.filter(email="boss@example.com").exists())

    def test_invited_user_sees_invitation_on_dashboard_and_can_decline(self):
        stranger = get_user_model().objects.create_user(email="guest@example.com", full_name="Гость", is_email_verified=True)
        invitation = invite_member(project=self.project, actor=self.customer, email="Guest@Example.com", role="executor")
        self.client.force_login(stranger)
        self.assertContains(self.client.get(reverse("workspace:dashboard")), "Квартира")
        response = self.client.post(reverse("workspace:accept", args=[invitation.token]), {"decision": "decline"})
        self.assertRedirects(response, reverse("workspace:dashboard"))
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "declined")
        self.assertFalse(ProjectMember.objects.filter(project=self.project, user=stranger).exists())

    # --- project deletion and archive ---

    def test_project_with_team_and_empty_stages_can_be_deleted(self):
        project = create_project(user=self.customer, name="Черновик")
        member = self.join(project, self.customer, self.foreman, "foreman")
        create_stage(project=project, actor=self.customer, name="План", start_date=self.today, end_date=self.today, responsible=member)
        invite_member(project=project, actor=self.customer, email="later@example.com", role="executor")
        self.client.force_login(self.customer)
        page = self.client.get(reverse("workspace:project", args=[project.pk]))
        self.assertContains(page, "Удалить проект")
        self.client.post(reverse("workspace:project-delete", args=[project.pk]))
        self.assertFalse(Project.objects.filter(pk=project.pk).exists())

    def test_project_with_work_is_archived_instead_and_can_be_restored(self):
        self.task(self.worker_member)
        self.client.force_login(self.customer)
        page = self.client.get(reverse("workspace:project", args=[self.project.pk]))
        self.assertContains(page, "в проекте есть задачи")  # the reason is shown, not a vanished button
        self.client.post(reverse("workspace:project-delete", args=[self.project.pk]))
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())
        self.client.post(reverse("workspace:project-archive", args=[self.project.pk]), {"action": "archive", "reason": ""})
        self.project.refresh_from_db()
        self.assertIsNone(self.project.archived_at)  # reason is required
        self.client.post(reverse("workspace:project-archive", args=[self.project.pk]), {"action": "archive", "reason": "Тестовый проект"})
        self.project.refresh_from_db()
        self.assertTrue(self.project.is_archived)
        card_link = f'href="{reverse("workspace:project", args=[self.project.pk])}"'
        self.assertNotContains(self.client.get(reverse("workspace:projects")), card_link)
        self.assertContains(self.client.get(reverse("workspace:projects") + "?archived=1"), card_link)
        with self.assertRaises(ValidationError):  # read-only while archived
            create_stage(project=self.project, actor=self.customer, name="Новый", start_date=self.today, end_date=self.today, responsible=self.foreman_member)
        restore_project(project=self.project, actor=self.customer)
        self.project.refresh_from_db()
        self.assertFalse(self.project.is_archived)

    def test_only_customer_archives(self):
        with self.assertRaises(Exception):
            archive_project(project=self.project, actor=self.foreman, reason="Нет")

    # --- creating a project ---

    def test_new_project_opens_and_becomes_current(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse("workspace:project-create"), {"name": "  Новая квартира  ", "area": "54.5", "budget": "300000"})
        project = Project.objects.get(name="Новая квартира")
        self.assertRedirects(response, reverse("workspace:project", args=[project.pk]))
        dashboard = self.client.get(reverse("workspace:dashboard"))
        self.assertEqual(dashboard.context["project"], project)  # not the oldest project any more
        self.assertContains(self.client.get(reverse("workspace:project", args=[project.pk])), "Что сделать дальше")

    def test_stage_without_tasks_cannot_be_handed_over(self):
        from apps.quality.services import submit_stage
        with self.assertRaises(ValidationError):
            submit_stage(stage=self.stage, actor=self.foreman)
        self.assertFalse(Task.objects.exists())
