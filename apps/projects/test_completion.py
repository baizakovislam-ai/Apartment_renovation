from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.estimates.services import create_draft
from apps.finances.services import create_expense
from apps.quality.models import Remark
from apps.repairs.models import Stage
from apps.repairs.services import create_stage
from apps.workspace.templatetags.workspace_ui import event_label
from .completion import complete_project
from .models import ProjectEvent, ProjectMember
from .services import archive_project, create_project, delete_project, invite_member, restore_project, update_project


class ProjectCompletionTests(TestCase):
    def setUp(self):
        user = get_user_model()
        self.customer = user.objects.create_user(email="customer@example.com", full_name="Customer", is_email_verified=True)
        self.foreman = user.objects.create_user(email="foreman@example.com", full_name="Foreman", is_email_verified=True)
        self.project = create_project(user=self.customer, name="Ремонт")
        self.project.status = self.project.Status.IN_PROGRESS
        self.project.save(update_fields=["status"])
        self.foreman_member = ProjectMember.objects.create(project=self.project, user=self.foreman, role=ProjectMember.Role.FOREMAN)
        today = timezone.localdate()
        self.stage = Stage.objects.create(
            project=self.project, name="Отделка", start_date=today, end_date=today + timedelta(days=1),
            responsible=self.foreman_member, created_by=self.foreman, status=Stage.Status.ACCEPTED,
        )

    def add_remark(self, status):
        return Remark.objects.create(
            project=self.project, stage=self.stage, description="Проверить поверхность", responsible=self.foreman_member,
            due_date=timezone.localdate() + timedelta(days=1), created_by=self.customer, status=status,
        )

    def test_completion_requires_every_non_cancelled_stage_accepted(self):
        self.stage.status = Stage.Status.IN_PROGRESS
        self.stage.save(update_fields=["status"])
        with self.assertRaisesMessage(ValidationError, "не приняты этапы"):
            complete_project(project=self.project, actor=self.customer)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, self.project.Status.IN_PROGRESS)

    def test_completion_blocks_open_or_unconfirmed_remarks(self):
        self.add_remark(Remark.Status.FIXED)
        with self.assertRaisesMessage(ValidationError, "нерешённых замечаний"):
            complete_project(project=self.project, actor=self.customer)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, self.project.Status.IN_PROGRESS)

    def test_customer_completes_project_and_event_is_recorded(self):
        self.stage.status = Stage.Status.CANCELLED
        self.stage.save(update_fields=["status"])
        Stage.objects.create(
            project=self.project, name="Электрика", start_date=timezone.localdate(), end_date=timezone.localdate(),
            responsible=self.foreman_member, created_by=self.foreman, status=Stage.Status.ACCEPTED,
        )
        complete_project(project=self.project, actor=self.customer)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, self.project.Status.COMPLETED)
        self.assertTrue(ProjectEvent.objects.filter(project=self.project, actor=self.customer, action="project.completed").exists())

    def test_only_customer_can_complete_project(self):
        with self.assertRaises(PermissionDenied):
            complete_project(project=self.project, actor=self.foreman)

    def test_empty_project_cannot_be_completed(self):
        self.stage.delete()
        with self.assertRaisesMessage(ValidationError, "без этапов"):
            complete_project(project=self.project, actor=self.customer)


class CompletedProjectReadOnlyTests(TestCase):
    """After FR18 the project is a final record: nothing in it may change, it can only be archived."""

    def setUp(self):
        ProjectCompletionTests.setUp(self)
        complete_project(project=self.project, actor=self.customer)
        self.project.refresh_from_db()

    def assert_blocked(self, call):
        with self.assertRaisesMessage(ValidationError, "Проект завершён"):
            call()

    def test_project_data_and_team_are_frozen(self):
        self.assert_blocked(lambda: update_project(project=self.project, actor=self.customer, name="Другое"))
        self.assert_blocked(lambda: invite_member(project=self.project, actor=self.customer, email="new@example.com", role="executor"))
        self.assert_blocked(lambda: delete_project(project=self.project, actor=self.customer))
        self.assertTrue(type(self.project).objects.filter(pk=self.project.pk).exists())

    def test_work_and_money_cannot_be_added(self):
        today = timezone.localdate()
        self.assert_blocked(lambda: create_stage(project=self.project, actor=self.foreman, name="Новый этап",
                                                 start_date=today, end_date=today, responsible=self.foreman_member))
        self.assert_blocked(lambda: create_expense(project=self.project, actor=self.foreman, amount=Decimal("1000"), date=today,
                                                   recipient="Магазин", purpose="Краска", category="materials"))
        self.assert_blocked(lambda: create_draft(project=self.project, actor=self.foreman))
        self.assertEqual(self.project.stages.count(), 1)
        self.assertFalse(self.project.expenses.exists())

    def test_repeated_completion_explains_itself(self):
        with self.assertRaisesMessage(ValidationError, "уже завершён"):
            complete_project(project=self.project, actor=self.customer)

    def test_completed_project_can_still_be_archived_and_restored(self):
        archive_project(project=self.project, actor=self.customer, reason="Ремонт сдан")
        restore_project(project=self.project, actor=self.customer)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, self.project.Status.COMPLETED)
        self.assertIsNone(self.project.archived_at)

    def test_history_shows_completion_in_russian(self):
        self.assertEqual(event_label("project.completed"), "Проект завершён")

    def test_pages_are_view_only(self):
        self.client.force_login(self.customer)
        page = self.client.get(reverse("workspace:project", args=[self.project.pk]))
        self.assertContains(page, "завершён — только просмотр")
        self.assertNotContains(page, reverse("workspace:project-delete", args=[self.project.pk]))
        self.assertNotContains(page, reverse("workspace:project-edit", args=[self.project.pk]))
        self.assertContains(page, "В архив")
        stages = self.client.get(reverse("workspace:stages", args=[self.project.pk]))
        self.assertNotContains(stages, reverse("workspace:stage-create", args=[self.project.pk]))
        edit = self.client.get(reverse("workspace:project-edit", args=[self.project.pk]))
        self.assertRedirects(edit, reverse("workspace:project", args=[self.project.pk]))
