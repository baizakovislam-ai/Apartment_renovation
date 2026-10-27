import io
import shutil
import tempfile
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from apps.estimates.testing import mark_started
from apps.projects.models import ProjectEvent, ProjectMember
from apps.projects.services import create_project
from .models import Photo, Task
from .services import create_stage
from .task_services import project_progress

TEMP_MEDIA = tempfile.mkdtemp()


def image_file(name="photo.png", image_format="PNG", size=(40, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 120, 40)).save(buffer, format=image_format)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{image_format.lower()}")


@override_settings(PRIVATE_MEDIA_ROOT=TEMP_MEDIA)
class TaskWorkflowTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        User = get_user_model()
        make = lambda email: User.objects.create_user(email=email, full_name=email.split("@")[0], is_email_verified=True)
        self.customer, self.foreman, self.executor, self.executor2, self.stranger = map(make, [
            "customer@example.com", "foreman@example.com", "executor@example.com", "executor2@example.com", "stranger@example.com"])
        self.project = create_project(user=self.customer, name="Ремонт")
        mark_started(self.project)  # works started: estimate approved (ТЗ 4.2)
        self.foreman_member = ProjectMember.objects.create(project=self.project, user=self.foreman, role="foreman")
        self.executor_member = ProjectMember.objects.create(project=self.project, user=self.executor, role="executor")
        self.executor2_member = ProjectMember.objects.create(project=self.project, user=self.executor2, role="executor")
        self.today = timezone.localdate()
        self.stage = create_stage(project=self.project, actor=self.customer, name="Электрика", start_date=self.today,
                                  end_date=self.today + timedelta(days=10), responsible=self.foreman_member)
        self.url = f"/api/projects/{self.project.pk}/tasks/"
        self.payload = {"stage": self.stage.pk, "name": "Кабель на кухне", "assignee": self.executor_member.pk,
                        "due_date": str(self.today + timedelta(days=3))}
        self.client.force_authenticate(self.foreman)

    def create_task(self, **extra):
        response = self.client.post(self.url, {**self.payload, **extra})
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def act(self, task_id, action, user, text=""):
        self.client.force_authenticate(user)
        version = Task.objects.get(pk=task_id).version
        return self.client.post(f"/api/tasks/{task_id}/action/", {"action": action, "version": version, "text": text})

    def start_stage(self):
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.patch(f"/api/stages/{self.stage.pk}/", {"status": "in_progress"}).status_code, 200)

    def test_manager_creates_task_and_history_is_recorded(self):
        data = self.create_task()
        self.assertEqual(data["status"], "todo")
        self.assertTrue(ProjectEvent.objects.filter(action="task.created", data__task_id=data["id"]).exists())

    def test_executor_cannot_create_and_validation_rules(self):
        self.client.force_authenticate(self.executor)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 403)
        self.client.force_authenticate(self.foreman)
        self.assertEqual(self.client.post(self.url, {**self.payload, "assignee": self.foreman_member.pk}).status_code, 400)
        self.assertEqual(self.client.post(self.url, {**self.payload, "due_date": str(self.today + timedelta(days=30))}).status_code, 400)
        other = create_project(user=self.stranger, name="Чужой")
        other_stage = create_stage(project=other, actor=self.stranger, name="Чужой этап", start_date=self.today, end_date=self.today, responsible=other.members.get())
        self.assertEqual(self.client.post(self.url, {**self.payload, "stage": other_stage.pk}).status_code, 400)
        self.assertFalse(Task.objects.exists())

    def test_repeated_request_id_does_not_create_duplicate(self):
        request_id = str(uuid.uuid4())
        first = self.create_task(request_id=request_id)
        second = self.client.post(self.url, {**self.payload, "request_id": request_id})
        self.assertEqual(second.data["id"], first["id"])
        self.assertEqual(Task.objects.count(), 1)

    def test_executor_sees_only_assigned_tasks_and_stranger_sees_nothing(self):
        mine = self.create_task()
        other = self.create_task(assignee=self.executor2_member.pk, name="Чужая задача")
        self.client.force_authenticate(self.executor)
        self.assertEqual([item["id"] for item in self.client.get(self.url).data], [mine["id"]])
        self.assertEqual(self.client.get(f"/api/tasks/{other['id']}/").status_code, 403)
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(f"/api/tasks/{mine['id']}/").status_code, 404)

    def test_full_status_cycle_with_return(self):
        task = self.create_task()
        self.assertEqual(self.act(task["id"], "start", self.executor).status_code, 400)  # stage not started
        self.start_stage()
        self.assertEqual(self.act(task["id"], "start", self.foreman).status_code, 403)
        self.assertEqual(self.act(task["id"], "start", self.executor).data["status"], "in_progress")
        self.assertEqual(self.act(task["id"], "submit", self.executor).status_code, 400)  # no result yet
        self.assertEqual(self.act(task["id"], "submit", self.executor, "Кабель проложен").data["status"], "review")
        self.assertEqual(self.act(task["id"], "approve", self.executor).status_code, 403)
        self.assertEqual(self.act(task["id"], "return", self.foreman).status_code, 400)  # reason required
        returned = self.act(task["id"], "return", self.foreman, "Добавьте фото щитка")
        self.assertEqual((returned.data["status"], returned.data["return_reason"]), ("in_progress", "Добавьте фото щитка"))
        self.act(task["id"], "submit", self.executor, "Исправлено")
        done = self.act(task["id"], "approve", self.customer)
        self.assertEqual(done.data["status"], "done")
        self.assertIsNotNone(Task.objects.get().approved_by)

    def test_stale_version_is_rejected(self):
        task = self.create_task()
        self.client.patch(f"/api/tasks/{task['id']}/", {"name": "Новое", "version": task["version"]})
        response = self.client.patch(f"/api/tasks/{task['id']}/", {"name": "Устаревшее", "version": task["version"]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Task.objects.get().name, "Новое")

    def test_delete_only_own_untouched_task(self):
        task = self.create_task()
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.delete(f"/api/tasks/{task['id']}/?version=1").status_code, 400)
        self.client.force_authenticate(self.foreman)
        self.assertEqual(self.client.delete(f"/api/tasks/{task['id']}/?version=1").status_code, 204)

    def test_stage_with_tasks_cannot_be_deleted_and_assignee_cannot_be_removed(self):
        self.create_task()
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.delete(f"/api/stages/{self.stage.pk}/").status_code, 400)
        self.assertEqual(self.client.delete(f"/api/projects/{self.project.pk}/members/{self.executor_member.pk}/").status_code, 400)

    def test_photo_report_upload_validation_and_private_access(self):
        task = self.create_task()
        reports = f"/api/tasks/{task['id']}/reports/"
        self.client.force_authenticate(self.executor)
        fake = SimpleUploadedFile("virus.png", b"not an image at all", content_type="image/png")
        self.assertEqual(self.client.post(reports, {"description": "Фото", "images": [fake]}, format="multipart").status_code, 400)
        gif = io.BytesIO()
        Image.new("RGB", (5, 5)).save(gif, format="GIF")
        self.assertEqual(self.client.post(reports, {"description": "Фото", "images": [SimpleUploadedFile("a.gif", gif.getvalue())]}, format="multipart").status_code, 400)
        response = self.client.post(reports, {"description": "Щиток собран", "images": [image_file(), image_file("b.jpg", "JPEG")]}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data["photos"]), 2)
        photo_url = response.data["photos"][0]["url"]
        self.assertNotIn("private", photo_url)
        self.client.force_login(self.executor)
        self.assertEqual(self.client.get(photo_url).status_code, 200)
        for user in [self.executor2, self.stranger]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(photo_url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(photo_url).status_code, 302)
        self.assertEqual(Photo.objects.count(), 2)

    def test_progress_counts_tasks_and_accepted_stages(self):
        self.assertEqual(project_progress(self.project)["percent"], 0)
        task = self.create_task()
        self.start_stage()
        self.act(task["id"], "start", self.executor)
        self.act(task["id"], "submit", self.executor, "Готово")
        self.act(task["id"], "approve", self.foreman)
        progress = project_progress(self.project)
        self.assertEqual((progress["percent"], progress["tasks_done"]), (90, 1))
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get(f"/api/projects/{self.project.pk}/progress/").data["percent"], 90)

    def test_deadline_cannot_be_set_in_the_past(self):
        self.stage.start_date = self.today - timedelta(days=5)
        self.stage.save()
        past = str(self.today - timedelta(days=1))
        response = self.client.post(self.url, {**self.payload, "due_date": past})
        self.assertEqual(response.status_code, 400)
        self.assertIn("прошлом", str(response.data))
        task = self.create_task()
        self.assertEqual(self.client.patch(f"/api/tasks/{task['id']}/", {"due_date": past, "version": task["version"]}).status_code, 400)
        # A task that is already overdue can still be edited without touching its date.
        Task.objects.filter(pk=task["id"]).update(due_date=self.today - timedelta(days=2))
        response = self.client.patch(f"/api/tasks/{task['id']}/", {"name": "Переименована", "due_date": str(self.today - timedelta(days=2)), "version": task["version"]})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["is_overdue"])
