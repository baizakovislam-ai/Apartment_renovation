from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from apps.projects.models import ProjectMember
from apps.projects.services import create_project
from apps.quality.models import Remark, StageDecision
from apps.reports.models import PhotoReport, ReportPhoto
from .models import Stage, Task


def image_upload(name="work.png"):
    stream = BytesIO()
    Image.new("RGB", (24, 24), "#8ebcb3").save(stream, "PNG")
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


class WorkflowTests(APITestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.media.cleanup)
        self.addCleanup(self.settings_override.disable)
        User = get_user_model()
        self.customer, self.foreman, self.executor, self.other, self.stranger = [
            User.objects.create_user(email=f"{name}@example.com", full_name=name, is_email_verified=True)
            for name in ["customer40", "foreman40", "executor40", "other40", "stranger40"]
        ]
        self.project = create_project(user=self.customer, name="Ремонт 40%")
        self.foreman_member = ProjectMember.objects.create(project=self.project, user=self.foreman, role="foreman")
        self.executor_member = ProjectMember.objects.create(project=self.project, user=self.executor, role="executor")
        self.other_member = ProjectMember.objects.create(project=self.project, user=self.other, role="executor")
        self.today = timezone.localdate()
        self.stage = Stage.objects.create(project=self.project, name="Отделка", created_by=self.customer,
            responsible=self.foreman_member, start_date=self.today - timedelta(days=5), end_date=self.today + timedelta(days=7), status="in_progress")
        self.list_url = f"/api/projects/{self.project.pk}/tasks/"
        self.payload = {"name": "Покрасить стены", "stage": self.stage.pk, "assignee": self.executor_member.pk, "due_date": str(self.today)}
        self.client.force_authenticate(self.customer)

    def task(self, **kwargs):
        values = {"project": self.project, "stage": self.stage, "name": "Покраска", "assignee": self.executor_member,
                  "due_date": self.today, "request_id": uuid4(), "created_by": self.customer}
        values.update(kwargs)
        return Task.objects.create(**values)

    def action(self, task, action, actor, **data):
        task.refresh_from_db()
        self.client.force_authenticate(actor)
        return self.client.post(f"/api/tasks/{task.pk}/action/", {"action": action, "version": task.version, **data}, format="json")

    def stage_action(self, action, actor, **data):
        self.stage.refresh_from_db()
        self.client.force_authenticate(actor)
        return self.client.post(f"/api/stages/{self.stage.pk}/acceptance/", {"action": action, "version": self.stage.acceptance_version, **data}, format="json")

    def remark(self):
        self.client.force_authenticate(self.customer)
        response = self.client.post(f"/api/projects/{self.project.pk}/remarks/", {"stage": self.stage.pk, "description": "Неровная окраска", "responsible": self.executor_member.pk, "due_date": str(self.today + timedelta(days=2))}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return Remark.objects.get(pk=response.data["id"])

    def test_task_crud_and_stale_update(self):
        response = self.client.post(self.list_url, self.payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        url = f"/api/tasks/{response.data['id']}/"
        self.assertEqual(self.client.patch(url, {"name": "Новая покраска", "version": 1}, format="json").status_code, 200)
        self.assertEqual(self.client.patch(url, {"name": "Потеря данных", "version": 1}, format="json").status_code, 400)
        self.assertEqual(self.client.get(url).data["name"], "Новая покраска")
        self.assertEqual(self.client.patch(url, {"name": "Без версии"}, format="json").status_code, 400)
        self.assertEqual(self.client.delete(url, {"version": 2}, format="json").status_code, 204)

    def test_duplicate_task_request_creates_one_record(self):
        payload = {**self.payload, "request_id": str(uuid4())}
        a = self.client.post(self.list_url, payload, format="json")
        b = self.client.post(self.list_url, payload, format="json")
        self.assertEqual((a.status_code, b.status_code), (201, 201), b.data)
        self.assertEqual(a.data["id"], b.data["id"])
        self.assertEqual(self.project.events.filter(action="task.created").count(), 1)

    def test_assignment_and_dates_must_match_project(self):
        other_project = create_project(user=self.stranger, name="Другой")
        other_stage = Stage.objects.create(project=other_project, name="Чужой этап", created_by=self.stranger)
        for changes in [{"stage": other_stage.pk}, {"assignee": self.foreman_member.pk}, {"assignee": other_project.members.get().pk}, {"due_date": str(self.today + timedelta(days=10))}, {"name": " "}]:
            response = self.client.post(self.list_url, {**self.payload, **changes}, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Task.objects.exists())

    def test_executor_only_sees_assigned_tasks(self):
        mine, other = self.task(), self.task(assignee=self.other_member)
        self.client.force_authenticate(self.executor)
        self.assertEqual([row["id"] for row in self.client.get(self.list_url).data], [mine.pk])
        self.assertEqual(self.client.get(f"/api/tasks/{other.pk}/").status_code, 404)
        self.assertEqual(self.client.post(self.list_url, self.payload).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/tasks/{mine.pk}/", {"name": "Подмена", "version": 1}).status_code, 403)

    def test_task_lifecycle_requires_assigned_executor_then_manager(self):
        task = self.task()
        self.assertEqual(self.action(task, "start", self.customer).status_code, 403)
        self.assertEqual(self.action(task, "start", self.executor).status_code, 200)
        self.assertEqual(self.action(task, "submit", self.executor).status_code, 400)
        self.assertEqual(self.action(task, "submit", self.executor, result="Стены окрашены").status_code, 200)
        self.assertEqual(self.action(task, "approve", self.executor).status_code, 403)
        self.assertEqual(self.action(task, "return", self.foreman).status_code, 400)
        self.assertEqual(self.action(task, "return", self.foreman, result="Исправить угол").status_code, 200)
        self.assertEqual(self.action(task, "submit", self.executor, result="Угол исправлен").status_code, 200)
        self.assertEqual(self.action(task, "approve", self.foreman).status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, "done")
        self.assertEqual(task.result, "Угол исправлен")

    def test_cannot_run_planned_stage_or_patch_status(self):
        self.stage.status = "planned"
        self.stage.save()
        task = self.task()
        self.assertEqual(self.action(task, "start", self.executor).status_code, 400)
        self.client.force_authenticate(self.customer)
        self.client.patch(f"/api/tasks/{task.pk}/", {"status": "done", "version": 1}, format="json")
        task.refresh_from_db()
        self.assertEqual(task.status, "todo")
        self.assertEqual(self.stage_action("submit", self.foreman).status_code, 400)

    def test_task_deletion_only_own_unstarted_draft(self):
        task = self.task()
        self.client.force_authenticate(self.foreman)
        self.assertEqual(self.client.delete(f"/api/tasks/{task.pk}/", {"version": 1}, format="json").status_code, 400)
        task.status = "in_progress"
        task.save()
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.delete(f"/api/tasks/{task.pk}/", {"version": 1}, format="json").status_code, 400)

    def test_photo_upload_and_private_access(self):
        task = self.task()
        self.client.force_authenticate(self.executor)
        response = self.client.post(f"/api/tasks/{task.pk}/reports/", {"description": "Первый слой", "images": [image_upload(), image_upload("second.webp")]}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data["photos"]), 2)
        photo_url = response.data["photos"][0]["url"]
        web = Client()
        web.force_login(self.executor)
        response = web.get(photo_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b"".join(response.streaming_content))
        response.close()
        web.force_login(self.other)
        self.assertEqual(web.get(photo_url).status_code, 404)
        web.force_login(self.stranger)
        self.assertEqual(web.get(photo_url).status_code, 404)
        web.logout()
        self.assertEqual(web.get(photo_url).status_code, 302)
        photo = ReportPhoto.objects.first()
        self.assertNotIn("work.png", photo.image.name)
        with photo.image.open("rb") as content:
            self.assertEqual(Image.open(content).format, "JPEG")
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.delete(f"/api/tasks/{task.pk}/", {"version": 1}, format="json").status_code, 400)

    def test_invalid_images_leave_no_report_or_files(self):
        task = self.task()
        for upload in [SimpleUploadedFile("bad.jpg", b"<script>fake</script>", content_type="image/jpeg"), image_upload("bad.svg")]:
            response = self.client.post(f"/api/tasks/{task.pk}/reports/", {"description": "Фото", "images": [image_upload(), upload]}, format="multipart")
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(PhotoReport.objects.exists())
        self.assertFalse(ReportPhoto.objects.exists())
        from pathlib import Path
        self.assertFalse(list(Path(self.media.name).rglob("*.jpg")))

    def test_duplicate_report_does_not_duplicate_files(self):
        task = self.task()
        request_id = str(uuid4())
        for _ in range(2):
            response = self.client.post(f"/api/tasks/{task.pk}/reports/", {"description": "Готово", "request_id": request_id, "images": [image_upload()]}, format="multipart")
            self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(PhotoReport.objects.count(), 1)
        self.assertEqual(ReportPhoto.objects.count(), 1)

    def test_photo_limits_and_closed_stage_are_enforced(self):
        task = self.task()
        url = f"/api/tasks/{task.pk}/reports/"
        response = self.client.post(url, {"description": "Слишком много", "images": [image_upload() for _ in range(9)]}, format="multipart")
        self.assertEqual(response.status_code, 400)
        from apps.reports.services import prepare_image
        from rest_framework.exceptions import ValidationError
        upload = image_upload()
        upload.size = 8 * 1024 * 1024 + 1
        with self.assertRaises(ValidationError):
            prepare_image(upload)
        self.stage.status = "pending_acceptance"
        self.stage.save()
        response = self.client.post(url, {"description": "Позднее изменение", "images": [image_upload()]}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PhotoReport.objects.exists())

    def test_new_report_invalidates_an_already_open_task_review(self):
        task = self.task(status="review")
        response = self.client.post(f"/api/tasks/{task.pk}/reports/", {"description": "Дополнительное фото", "images": [image_upload()]}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(f"/api/tasks/{task.pk}/action/", {"action": "approve", "version": 1}, format="json")
        self.assertEqual(response.status_code, 400)
        task.refresh_from_db()
        self.assertEqual(task.status, "review")

    def test_remark_blocks_acceptance_until_customer_closes(self):
        self.task(status="done")
        self.assertEqual(self.stage_action("submit", self.customer).status_code, 403)
        self.assertEqual(self.stage_action("submit", self.foreman).status_code, 200)
        remark = self.remark()
        self.assertEqual(self.stage_action("accept", self.customer).status_code, 400)
        self.assertEqual(self.stage_action("submit", self.foreman).status_code, 400)
        url = f"/api/projects/{self.project.pk}/remarks/{remark.pk}/action/"
        self.client.force_authenticate(self.executor)
        response = self.client.post(url, {"action": "fix", "version": 1, "text": "Перекрасил угол"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.client.post(url, {"action": "close", "version": 2}).status_code, 403)
        self.assertEqual(self.stage_action("submit", self.foreman).status_code, 400)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.post(url, {"action": "close", "version": 2}).status_code, 200)
        remark.refresh_from_db()
        self.assertEqual(remark.closed_by, self.customer)
        self.assertIsNotNone(remark.closed_at)
        self.assertEqual(self.stage_action("submit", self.foreman).status_code, 200)
        self.assertEqual(self.stage_action("accept", self.foreman).status_code, 403)
        self.assertEqual(self.stage_action("accept", self.customer).status_code, 200)
        self.assertEqual(StageDecision.objects.filter(action="accept").count(), 1)
        self.assertEqual(self.stage_action("accept", self.customer).status_code, 400)

    def test_acceptance_rechecks_remarks_even_if_stage_is_pending(self):
        self.task(status="done")
        self.stage.status = "pending_acceptance"
        self.stage.save()
        Remark.objects.create(project=self.project, stage=self.stage, description="Дефект", responsible=self.executor_member,
            due_date=self.today, request_id=uuid4(), created_by=self.customer)
        self.assertEqual(self.stage_action("accept", self.customer).status_code, 400)

    def test_incomplete_and_empty_stage_cannot_be_submitted(self):
        self.assertEqual(self.stage_action("submit", self.foreman).status_code, 400)
        self.task(status="review")
        self.assertEqual(self.stage_action("submit", self.foreman).status_code, 400)

    def test_return_requires_reason_and_stale_acceptance_is_rejected(self):
        self.task(status="done")
        self.assertEqual(self.stage_action("submit", self.foreman).status_code, 200)
        self.assertEqual(self.stage_action("return", self.customer).status_code, 400)
        self.assertEqual(self.stage_action("return", self.customer, reason="Проверить поверхность").status_code, 200)
        self.assertEqual(self.stage_action("submit", self.foreman).status_code, 200)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.post(f"/api/stages/{self.stage.pk}/acceptance/", {"action": "accept", "version": 2}).status_code, 400)

    def test_remarks_are_isolated_and_versioned(self):
        remark = self.remark()
        url = f"/api/projects/{self.project.pk}/remarks/{remark.pk}/action/"
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/projects/{self.project.pk}/remarks/").data, [])
        self.assertEqual(self.client.post(url, {"action": "reply", "version": 1, "text": "Чужой ответ"}).status_code, 404)
        self.client.force_authenticate(self.executor)
        self.assertEqual(self.client.post(url, {"action": "reply", "version": 1, "text": "Уточните цвет"}).status_code, 200)
        self.assertEqual(self.client.post(url, {"action": "fix", "version": 1, "text": "Исправлено"}).status_code, 400)
        self.assertEqual(remark.replies.count(), 1)

    def test_progress_counts_only_accepted_non_cancelled_stages(self):
        self.task(status="done")
        Stage.objects.create(project=self.project, name="Принятый", status="accepted", created_by=self.customer)
        Stage.objects.create(project=self.project, name="Отменённый", status="cancelled", created_by=self.customer)
        response = self.client.get(f"/api/projects/{self.project.pk}/progress/")
        self.assertEqual(response.data, {"percent": 50, "stages": 2, "accepted": 1, "tasks": 1, "done": 1, "overdue": 0})
        self.stage.status = "pending_acceptance"
        self.stage.save()
        self.assertEqual(self.client.get(f"/api/projects/{self.project.pk}/progress/").data["percent"], 50)

    def test_dependent_records_prevent_stage_or_member_deletion(self):
        self.stage.status = "planned"
        self.stage.save()
        self.task()
        self.assertEqual(self.client.delete(f"/api/stages/{self.stage.pk}/").status_code, 400)
        self.assertEqual(self.client.delete(f"/api/projects/{self.project.pk}/members/{self.executor_member.pk}/").status_code, 400)

    def test_stage_dates_cannot_exclude_existing_tasks(self):
        self.task()
        response = self.client.patch(f"/api/stages/{self.stage.pk}/", {"end_date": str(self.today - timedelta(days=1))})
        self.assertEqual(response.status_code, 400, response.data)

    def test_workspace_pages_render_for_all_roles(self):
        task = self.task()
        self.remark()
        web = Client()
        for actor in [self.customer, self.foreman, self.executor]:
            web.force_login(actor)
            for section in ["tasks", "reports", "remarks", "project-detail"]:
                response = web.get(reverse("workspace:" + section, args=[self.project.pk]))
                self.assertEqual(response.status_code, 200, (actor, section))
            self.assertEqual(web.get(reverse("workspace:task-detail", args=[self.project.pk, task.pk])).status_code, 200)
            expected = 403 if actor == self.executor else 200
            self.assertEqual(web.get(reverse("workspace:acceptance", args=[self.project.pk])).status_code, expected)
        web.force_login(self.customer)
        for section in ["task-create", "remark-create"]:
            self.assertEqual(web.get(reverse("workspace:" + section, args=[self.project.pk])).status_code, 200)

    def test_workspace_submission_creates_task_and_report(self):
        web = Client()
        web.force_login(self.customer)
        response = web.post(reverse("workspace:task-create", args=[self.project.pk]), {**self.payload, "request_id": str(uuid4())})
        self.assertEqual(response.status_code, 302)
        task = Task.objects.get()
        web.force_login(self.executor)
        response = web.post(reverse("workspace:task-detail", args=[self.project.pk, task.pk]), {"request_id": str(uuid4()), "description": "Готово", "images": [image_upload(), image_upload("another.png")]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ReportPhoto.objects.count(), 2)

    def test_strangers_and_anonymous_cannot_access_workflow(self):
        task = self.task()
        self.client.force_authenticate(self.stranger)
        for url in [self.list_url, f"/api/tasks/{task.pk}/", f"/api/tasks/{task.pk}/reports/", f"/api/projects/{self.project.pk}/progress/", f"/api/projects/{self.project.pk}/remarks/"]:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        web = Client(enforce_csrf_checks=True)
        web.force_login(self.customer)
        self.assertEqual(web.post(reverse("workspace:task-create", args=[self.project.pk]), self.payload).status_code, 403)
