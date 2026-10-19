from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.projects.models import ProjectEvent, ProjectMember
from apps.projects.services import create_project
from .models import Stage


class StageAPITests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.customer = User.objects.create_user(username="customer")
        self.foreman = User.objects.create_user(username="foreman")
        self.executor = User.objects.create_user(username="executor")
        self.stranger = User.objects.create_user(username="stranger")
        self.project = create_project(user=self.customer, name="Ремонт")
        self.customer_member = self.project.members.get(user=self.customer)
        self.foreman_member = ProjectMember.objects.create(project=self.project, user=self.foreman, role="foreman")
        self.executor_member = ProjectMember.objects.create(project=self.project, user=self.executor, role="executor")
        self.url = f"/api/projects/{self.project.pk}/stages/"
        self.payload = {"name": "Электрика", "description": "Прокладка кабеля", "start_date": "2026-10-20", "end_date": "2026-10-25", "responsible": self.foreman_member.pk}
        self.client.force_authenticate(self.customer)

    def create_stage(self):
        response = self.client.post(self.url, self.payload)
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def test_stage_crud_and_history(self):
        response = self.create_stage()
        detail = f"/api/stages/{response.data['id']}/"
        self.assertEqual(response.data["created_by"], self.customer.pk)
        self.assertEqual(self.client.patch(detail, {"name": "Новая электрика"}).status_code, 200)
        self.assertEqual(self.client.get(self.url).data[0]["name"], "Новая электрика")
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.delete(detail).status_code, 204)
        self.assertEqual(list(ProjectEvent.objects.filter(action__startswith="stage.").order_by("id").values_list("action", flat=True)), ["stage.created", "stage.updated", "stage.deleted"])

    def test_foreman_can_manage_but_executor_can_only_read(self):
        self.client.force_authenticate(self.foreman)
        response = self.create_stage()
        detail = f"/api/stages/{response.data['id']}/"
        self.assertEqual(self.client.patch(detail, {"description": "Обновление"}).status_code, 200)
        self.client.force_authenticate(self.executor)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 403)
        self.assertEqual(self.client.patch(detail, {"name": "Подмена"}).status_code, 403)
        self.assertEqual(self.client.delete(detail).status_code, 403)

    def test_stranger_and_anonymous_cannot_access_stages(self):
        response = self.create_stage()
        detail = f"/api/stages/{response.data['id']}/"
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.patch(detail, {"name": "Подмена"}).status_code, 404)
        self.assertEqual(self.client.delete(detail).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_invalid_dates_on_creation_and_partial_update(self):
        response = self.client.post(self.url, {**self.payload, "end_date": "2026-10-01"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Stage.objects.exists())
        created = self.create_stage()
        self.assertEqual(self.client.patch(f"/api/stages/{created.data['id']}/", {"start_date": "2026-11-01"}).status_code, 400)
        self.assertEqual(ProjectEvent.objects.filter(action="stage.updated").count(), 0)

    def test_responsible_must_belong_to_same_project(self):
        other_project = create_project(user=self.stranger, name="Другой")
        other_member = other_project.members.get()
        self.assertEqual(self.client.post(self.url, {**self.payload, "responsible": other_member.pk}).status_code, 400)
        self.assertEqual(self.client.post(self.url, {**self.payload, "responsible": None}, format="json").status_code, 400)

    def test_assigned_member_cannot_be_removed_or_have_role_changed(self):
        self.create_stage()
        url = f"/api/projects/{self.project.pk}/members/{self.foreman_member.pk}/"
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.assertEqual(self.client.patch(url, {"role": "executor"}).status_code, 400)

    def test_only_author_can_delete_draft(self):
        response = self.create_stage()
        self.client.force_authenticate(self.foreman)
        self.assertEqual(self.client.delete(f"/api/stages/{response.data['id']}/").status_code, 400)
        self.assertTrue(Stage.objects.filter(pk=response.data["id"]).exists())

    def test_status_transitions_and_deletion_restrictions(self):
        self.assertEqual(self.client.post(self.url, {**self.payload, "status": "accepted"}).status_code, 400)
        response = self.create_stage()
        url = f"/api/stages/{response.data['id']}/"
        self.assertEqual(self.client.patch(url, {"status": "completed"}).status_code, 400)
        self.assertEqual(self.client.patch(url, {"status": "in_progress"}).status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.assertEqual(self.client.patch(url, {"status": "completed"}).status_code, 200)
        self.assertEqual(self.client.patch(url, {"status": "accepted"}).status_code, 400)

    def test_accepted_stage_is_immutable(self):
        response = self.create_stage()
        Stage.objects.filter(pk=response.data["id"]).update(status="accepted")
        url = f"/api/stages/{response.data['id']}/"
        self.assertEqual(self.client.patch(url, {"name": "Подмена"}).status_code, 400)
        self.assertEqual(self.client.delete(url).status_code, 400)

    def test_project_and_author_cannot_be_reassigned(self):
        other = create_project(user=self.stranger, name="Другой")
        response = self.client.post(self.url, {**self.payload, "project": other.pk, "created_by": self.stranger.pk})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["project"], self.project.pk)
        self.assertEqual(response.data["created_by"], self.customer.pk)

    def test_membership_removal_revokes_stage_access(self):
        response = self.create_stage()
        self.executor_member.delete()
        self.client.force_authenticate(self.executor)
        self.assertEqual(self.client.get(f"/api/stages/{response.data['id']}/").status_code, 404)
