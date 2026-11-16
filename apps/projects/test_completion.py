from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.quality.models import Remark
from apps.repairs.models import Stage
from .completion import complete_project
from .models import ProjectEvent, ProjectMember
from .services import create_project


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
