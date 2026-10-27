import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase
from django.core.management import call_command
from django.test import override_settings
from io import StringIO

from .models import ProjectEvent, ProjectInvitation, ProjectMember
from .services import create_project


class ProjectAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.customer = User.objects.create_user(full_name="customer", is_email_verified=True, email="owner@example.com", password="test-password")
        cls.foreman = User.objects.create_user(full_name="foreman", is_email_verified=True, email="foreman@example.com", password="test-password")
        cls.executor = User.objects.create_user(full_name="executor", is_email_verified=True, email="worker@example.com", password="test-password")
        cls.stranger = User.objects.create_user(full_name="stranger", is_email_verified=True, email="other@example.com", password="test-password")
        cls.project = create_project(user=cls.customer, name="Ремонт квартиры")
        cls.base = f"/api/projects/{cls.project.pk}/"

    def setUp(self):
        self.client.force_authenticate(self.customer)

    def invite(self, user=None, role="foreman"):
        return self.client.post(self.base + "invitations/", {"email": (user or self.foreman).email, "role": role})

    def join(self, user, role):
        return ProjectMember.objects.create(project=self.project, user=user, role=role)

    def test_project_creation_adds_customer_and_history(self):
        response = self.client.post("/api/projects/", {"name": "Новый объект"})
        self.assertEqual(response.status_code, 201)
        member = ProjectMember.objects.get(project_id=response.data["id"])
        self.assertEqual((member.user, member.role), (self.customer, "customer"))
        self.assertTrue(ProjectEvent.objects.filter(project=member.project, action="project.created").exists())

    def test_anonymous_access_is_denied(self):
        self.client.force_authenticate(None)
        for url in ["/api/projects/", self.base, self.base + "members/", self.base + "invitations/"]:
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_stranger_cannot_read_or_mutate_project(self):
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get("/api/projects/").data, [])
        for url in [self.base, self.base + "members/", self.base + "invitations/", self.base + "events/"]:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.invite().status_code, 404)

    def test_invite_accept_flow_and_replay(self):
        response = self.invite()
        self.assertEqual(response.status_code, 201)
        self.assertFalse(self.project.members.filter(user=self.foreman).exists())
        self.client.force_authenticate(self.foreman)
        url = f"/api/invitations/{response.data['token']}/accept/"
        accepted = self.client.post(url)
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(accepted.data["role"], "foreman")
        self.assertEqual(self.client.get(self.base).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 400)
        invitation = ProjectInvitation.objects.get(pk=response.data["id"])
        self.assertEqual(invitation.status, "accepted")
        self.assertIsNotNone(invitation.accepted_at)

    def test_wrong_account_cannot_accept(self):
        response = self.invite()
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.post(f"/api/invitations/{response.data['token']}/accept/").status_code, 400)
        self.assertFalse(self.project.members.filter(user=self.stranger).exists())

    def test_expired_invitation_cannot_be_accepted_and_can_be_replaced(self):
        response = self.invite()
        ProjectInvitation.objects.filter(pk=response.data["id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get(self.base + "invitations/").data[0]["status"], "expired")
        self.client.force_authenticate(self.foreman)
        self.assertEqual(self.client.post(f"/api/invitations/{response.data['token']}/accept/").status_code, 400)
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.invite().status_code, 201)

    def test_email_is_normalized(self):
        response = self.client.post(self.base + "invitations/", {"email": "FOREMAN@EXAMPLE.COM", "role": "foreman"})
        self.assertEqual(response.status_code, 201)
        self.client.force_authenticate(self.foreman)
        self.assertEqual(self.client.post(f"/api/invitations/{response.data['token']}/accept/").status_code, 201)

    def test_only_customer_manages_team_but_foreman_invites_executors(self):
        member = self.join(self.foreman, "foreman")
        self.client.force_authenticate(self.foreman)
        self.assertEqual(self.client.get(self.base + "members/").status_code, 200)
        self.assertEqual(self.client.get(self.base + "invitations/").status_code, 200)
        self.assertEqual(self.invite(self.stranger, "foreman").status_code, 403)  # foreman invites executors only
        self.assertEqual(self.invite(self.executor, "executor").status_code, 201)
        url = self.base + f"members/{member.pk}/"
        self.assertEqual(self.client.patch(url, {"role": "executor"}).status_code, 403)
        self.assertEqual(self.client.delete(url).status_code, 403)
        executor_member = self.join(self.stranger, "executor")
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get(self.base + "invitations/").status_code, 403)
        self.assertTrue(executor_member.is_active)

    def test_duplicates_and_second_foreman_are_rejected(self):
        self.assertEqual(self.invite().status_code, 201)
        self.assertEqual(self.invite().status_code, 400)
        self.assertEqual(self.invite(self.stranger).status_code, 400)
        self.assertEqual(self.project.invitations.count(), 1)
        self.assertEqual(ProjectEvent.objects.filter(action="invitation.created").count(), 1)

    def test_existing_foreman_and_existing_member_cannot_be_invited(self):
        self.join(self.foreman, "foreman")
        self.assertEqual(self.invite(self.stranger).status_code, 400)
        self.assertEqual(self.invite(self.foreman, "executor").status_code, 400)

    def test_cancel_invitation(self):
        response = self.invite()
        self.assertEqual(self.client.post(self.base + f"invitations/{response.data['id']}/cancel/").status_code, 204)
        self.client.force_authenticate(self.foreman)
        self.assertEqual(self.client.post(f"/api/invitations/{response.data['token']}/accept/").status_code, 400)

    def test_customer_cannot_be_removed_or_demoted(self):
        member = self.project.members.get(user=self.customer)
        url = self.base + f"members/{member.pk}/"
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.assertEqual(self.client.patch(url, {"role": "executor"}).status_code, 400)
        member.refresh_from_db()
        self.assertEqual(member.role, "customer")

    def test_role_change_and_removal_revoke_access(self):
        member = self.join(self.foreman, "foreman")
        url = self.base + f"members/{member.pk}/"
        self.assertEqual(self.client.patch(url, {"role": "executor"}).status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.client.force_authenticate(self.foreman)
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_pending_foreman_invitation_prevents_role_promotion(self):
        member = self.join(self.executor, "executor")
        self.invite()
        response = self.client.patch(self.base + f"members/{member.pk}/", {"role": "foreman"})
        self.assertEqual(response.status_code, 400)

    def test_members_and_invitations_are_scoped_to_project(self):
        other = create_project(user=self.stranger, name="Чужой")
        member = other.members.get()
        self.assertEqual(self.client.delete(self.base + f"members/{member.pk}/").status_code, 404)
        self.assertEqual(self.client.post(self.base + "invitations/", {"email": "a@example.com", "role": "customer"}).status_code, 400)

    def test_database_enforces_unique_members_and_roles(self):
        self.join(self.foreman, "foreman")
        for user, role in [(self.stranger, "customer"), (self.stranger, "foreman"), (self.foreman, "executor"), (self.stranger, "invalid")]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                ProjectMember.objects.create(project=self.project, user=user, role=role)

    def test_events_are_not_visible_to_executor(self):
        self.join(self.executor, "executor")
        self.client.force_authenticate(self.executor)
        self.assertEqual(self.client.get(self.base + "events/").status_code, 403)

    def test_real_session_login(self):
        self.client.force_authenticate(None)
        self.assertTrue(self.client.login(username="owner@example.com", password="test-password"))
        self.assertEqual(self.client.get(self.base).status_code, 200)

    def test_session_mutations_require_csrf_token(self):
        client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(client.login(username="owner@example.com", password="test-password"))
        self.assertEqual(client.post("/api/projects/", {"name": "Без CSRF"}).status_code, 403)
        client.get("/api/auth/login/")
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post("/api/projects/", {"name": "С CSRF"}, HTTP_X_CSRFTOKEN=token).status_code, 201)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"], PRIVATE_MEDIA_ROOT=tempfile.mkdtemp())
class DemoCommandTests(APITestCase):
    def test_demo_creates_team_and_stage_and_is_repeatable(self):
        output = StringIO()
        call_command("seed_demo", password="Demo-test-password", stdout=output)
        from .models import Project
        project = Project.objects.get(name="Демонстрационный ремонт")
        self.assertEqual(project.members.count(), 3)
        self.assertEqual(project.stages.count(), 5)
        self.assertTrue(self.client.login(username="foreman@example.com", password="Demo-test-password"))
        self.assertEqual(self.client.get(f"/api/projects/{project.pk}/stages/").status_code, 200)
        call_command("seed_demo", password="Another-password", stdout=output)
        self.assertEqual(Project.objects.count(), 1)
        self.assertTrue(self.client.login(username="foreman@example.com", password="Demo-test-password"))
