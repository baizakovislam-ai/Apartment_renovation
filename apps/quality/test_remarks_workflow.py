"""Remarks end to end: authors, photos, fix and confirmation, edit and withdraw."""
import io
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.estimates.testing import mark_started
from apps.projects.models import ProjectMember
from apps.projects.services import create_project
from apps.repairs.services import create_stage, update_stage
from apps.repairs.task_services import create_task
from .models import Remark, RemarkPhoto

TEMP_MEDIA = tempfile.mkdtemp()


def photo(name="defect.jpg"):
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (180, 30, 30)).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


@override_settings(PRIVATE_MEDIA_ROOT=TEMP_MEDIA, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class RemarkWorkflowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        User = get_user_model()
        make = lambda email: User.objects.create_user(email=email, full_name=email.split("@")[0], is_email_verified=True)
        self.customer, self.foreman, self.worker, self.worker2 = map(make, ["c@example.com", "f@example.com", "w@example.com", "w2@example.com"])
        self.project = create_project(user=self.customer, name="Ремонт")
        mark_started(self.project)  # works started: estimate approved (ТЗ 4.2)
        self.foreman_member = ProjectMember.objects.create(project=self.project, user=self.foreman, role="foreman")
        self.worker_member = ProjectMember.objects.create(project=self.project, user=self.worker, role="executor")
        self.worker2_member = ProjectMember.objects.create(project=self.project, user=self.worker2, role="executor")
        self.today = timezone.localdate()
        self.stage = create_stage(project=self.project, actor=self.foreman, name="Плитка", start_date=self.today,
                                  end_date=self.today + timedelta(days=5), responsible=self.foreman_member)
        self.planned = create_stage(project=self.project, actor=self.foreman, name="Потолки", start_date=self.today,
                                    end_date=self.today + timedelta(days=5), responsible=self.foreman_member)
        update_stage(stage=self.stage, actor=self.foreman, status="in_progress")
        self.task = create_task(project=self.project, actor=self.foreman, stage=self.stage, name="Ванная", assignee=self.worker_member, due_date=self.today)
        self.create_url = reverse("workspace:remark-create", args=[self.project.pk])

    def create(self, user, **extra):
        self.client.force_login(user)
        data = {"stage": self.stage.pk, "task": self.task.pk, "description": "Шов неровный", "responsible": self.worker_member.pk,
                "due_date": (self.today + timedelta(days=2)).isoformat(), **extra}
        return self.client.post(self.create_url, data)

    def act(self, user, remark, action, text="", **files):
        self.client.force_login(user)
        remark.refresh_from_db()
        return self.client.post(reverse("workspace:remark-action", args=[self.project.pk, remark.pk]),
                                {"action": action, "version": remark.version, "text": text, **files}, follow=True)

    def test_customer_creates_remark_with_photo_and_executor_fixes_with_photo(self):
        response = self.create(self.customer, images=[photo(), photo("second.jpg")])
        remark = Remark.objects.get()
        self.assertRedirects(response, reverse("workspace:remark", args=[self.project.pk, remark.pk]))
        self.assertEqual((remark.task, remark.photos.count()), (self.task, 2))
        photo_url = reverse("remark-photo-file", args=[remark.photos.first().pk])
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(photo_url).status_code, 200)
        self.client.force_login(self.worker2)
        self.assertEqual(self.client.get(photo_url).status_code, 404)  # not their remark
        self.assertEqual(self.client.get(reverse("workspace:remark", args=[self.project.pk, remark.pk])).status_code, 404)
        self.act(self.worker, remark, "reply", "Переделаю завтра")
        page = self.act(self.worker, remark, "fix", "Шов переложен", images=[photo("fixed.jpg")])
        self.assertContains(page, "Шов переложен")
        remark.refresh_from_db()
        self.assertEqual(remark.status, "fixed")
        self.assertEqual(RemarkPhoto.objects.filter(comment__isnull=False).count(), 1)
        self.assertContains(self.act(self.worker, remark, "close"), "Подтвердить устранение может автор замечания или заказчик")
        self.act(self.customer, remark, "close")
        remark.refresh_from_db()
        self.assertEqual((remark.status, remark.closed_by), ("closed", self.customer))

    def test_foreman_writes_remarks_to_executors_and_confirms_them(self):
        self.assertEqual(self.create(self.foreman, responsible=self.foreman_member.pk).status_code, 200)  # form error: executors only
        self.assertFalse(Remark.objects.exists())
        self.create(self.foreman)
        remark = Remark.objects.get()
        self.assertEqual(remark.created_by, self.foreman)
        self.act(self.worker, remark, "fix")
        self.act(self.foreman, remark, "reopen", "Скол остался")
        remark.refresh_from_db()
        self.assertEqual(remark.status, "open")
        self.act(self.worker, remark, "fix")
        self.act(self.foreman, remark, "close")  # the author confirms
        remark.refresh_from_db()
        self.assertEqual(remark.status, "closed")

    def test_executor_cannot_create_and_planned_stage_is_rejected(self):
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(self.create_url).status_code, 403)
        self.create(self.customer, stage=self.planned.pk, task="")
        self.assertFalse(Remark.objects.exists())

    def test_author_edits_and_withdraws(self):
        self.create(self.customer)
        remark = Remark.objects.get()
        edit_url = reverse("workspace:remark-edit", args=[self.project.pk, remark.pk])
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(edit_url).status_code, 403)
        self.client.force_login(self.customer)
        response = self.client.post(edit_url, {"description": "Шов неровный у окна", "responsible": self.worker2_member.pk,
                                               "due_date": (self.today + timedelta(days=3)).isoformat(), "version": remark.version})
        self.assertRedirects(response, reverse("workspace:remark", args=[self.project.pk, remark.pk]))
        remark.refresh_from_db()
        self.assertEqual((remark.description, remark.responsible), ("Шов неровный у окна", self.worker2_member))
        self.assertContains(self.act(self.customer, remark, "withdraw"), "Укажите, почему замечание снимается")
        self.act(self.customer, remark, "withdraw", "Создано по ошибке")
        remark.refresh_from_db()
        self.assertTrue(remark.withdrawn)
        self.assertEqual(remark.status, "closed")

    def test_remark_list_filters_and_pages_render(self):
        self.create(self.customer)
        self.create(self.customer, description="Пятно на стене", responsible=self.worker2_member.pk, request_id="")
        self.client.force_login(self.customer)
        url = reverse("workspace:remarks", args=[self.project.pk])
        self.assertContains(self.client.get(url), "Пятно на стене")
        self.assertNotContains(self.client.get(url + "?q=Шов"), "Пятно на стене")
        self.client.force_login(self.worker)
        response = self.client.get(url)
        self.assertContains(response, "Шов неровный")
        self.assertNotContains(response, "Пятно на стене")  # only remarks assigned to them
