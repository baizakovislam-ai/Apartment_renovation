from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.projects.models import Project, ProjectInvitation, ProjectMember
from apps.projects.services import create_project
from apps.repairs.models import Stage
from apps.repairs.services import create_stage


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class WorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(email="owner@example.com", full_name="Иван Заказчик", password="Test-pass-4587", is_email_verified=True)
        cls.foreman = User.objects.create_user(email="foreman@example.com", full_name="Прораб", is_email_verified=True)
        cls.worker = User.objects.create_user(email="worker@example.com", full_name="Исполнитель", is_email_verified=True)
        cls.outsider = User.objects.create_user(email="other@example.com", full_name="Посторонний", is_email_verified=True)
        cls.project = create_project(user=cls.owner, name="Квартира на Киевской")
        cls.foreman_member = ProjectMember.objects.create(project=cls.project, user=cls.foreman, role="foreman")
        ProjectMember.objects.create(project=cls.project, user=cls.worker, role="executor")
        cls.stage = create_stage(project=cls.project, actor=cls.owner, name="Электрика", start_date="2026-10-20", end_date="2026-10-25", responsible=cls.foreman_member)

    def setUp(self):
        self.client.force_login(self.owner)

    def stage_payload(self):
        return {"name": "Штукатурка", "description": "Стены", "start_date": "2026-10-21", "end_date": "2026-10-25", "responsible": self.foreman_member.pk, "position": 2, "status": "planned"}

    def test_workspace_pages_render_for_owner(self):
        for name, args in [("dashboard", []), ("projects", []), ("project-create", []), ("stages", [self.project.pk]), ("members", [self.project.pk]), ("history", [self.project.pk]), ("stage-create", [self.project.pk]), ("stage-edit", [self.project.pk, self.stage.pk])]:
            with self.subTest(page=name):
                response = self.client.get(reverse("workspace:" + name, args=args))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "workspace/design.css")
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)

    def test_dashboard_has_real_counts_and_no_mock_budget(self):
        response = self.client.get(reverse("workspace:dashboard"))
        self.assertEqual(response.context["project"].member_count, 3)
        self.assertEqual(response.context["total_stages"], 1)
        self.assertNotContains(response, "1 500 000")

    def test_no_projects_empty_state(self):
        self.client.force_login(self.outsider)
        self.assertContains(self.client.get("/"), "Ваш первый ремонт начинается здесь")

    def test_anonymous_is_redirected_to_login_and_preserves_invitation_url(self):
        self.client.logout()
        response = self.client.get("/")
        self.assertRedirects(response, "/accounts/login/?next=/", fetch_redirect_response=False)
        response = self.client.get("/invitations/example-token/")
        self.assertIn("next=/invitations/example-token/", response.url)

    def test_project_creation_adds_customer(self):
        response = self.client.post(reverse("workspace:project-create"), {"name": "Новый объект"})
        project = Project.objects.get(name="Новый объект")
        self.assertRedirects(response, reverse("workspace:members", args=[project.pk]))
        self.assertEqual(project.members.get().user, self.owner)

    def test_create_update_stage_and_dates(self):
        response = self.client.post(reverse("workspace:stage-create", args=[self.project.pk]), self.stage_payload())
        self.assertRedirects(response, reverse("workspace:stages", args=[self.project.pk]))
        stage = Stage.objects.get(name="Штукатурка")
        response = self.client.post(reverse("workspace:stage-edit", args=[self.project.pk, stage.pk]), {**self.stage_payload(), "name": "Новая штукатурка"})
        self.assertEqual(response.status_code, 302)
        stage.refresh_from_db()
        self.assertEqual(stage.name, "Новая штукатурка")
        response = self.client.post(reverse("workspace:stage-create", args=[self.project.pk]), {**self.stage_payload(), "end_date": "2026-10-01"})
        self.assertContains(response, "Окончание не может быть раньше начала")
        self.assertEqual(Stage.objects.count(), 2)

    def test_executor_has_no_management_controls_or_mutation_rights(self):
        self.client.force_login(self.worker)
        self.assertNotContains(self.client.get(reverse("workspace:stages", args=[self.project.pk])), "Добавить этап")
        self.assertNotContains(self.client.get(reverse("workspace:members", args=[self.project.pk])), "Создать приглашение")
        self.assertEqual(self.client.post(reverse("workspace:stage-create", args=[self.project.pk]), self.stage_payload()).status_code, 403)
        self.assertEqual(self.client.get(reverse("workspace:history", args=[self.project.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("workspace:members", args=[self.project.pk]), {"email": "x@example.com", "role": "executor"}).status_code, 403)
        response = self.client.post(reverse("workspace:stage-delete", args=[self.project.pk, self.stage.pk]), follow=True)
        self.assertContains(response, "Недостаточно прав")
        self.assertTrue(Stage.objects.filter(pk=self.stage.pk).exists())

    def test_foreign_project_is_inaccessible(self):
        self.client.force_login(self.outsider)
        for name in ["members", "stages", "history", "stage-create"]:
            self.assertEqual(self.client.get(reverse("workspace:" + name, args=[self.project.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("workspace:projects")), self.project.name)

    def test_invitation_full_flow_and_token_privacy(self):
        url = reverse("workspace:members", args=[self.project.pk])
        response = self.client.post(url, {"email": self.outsider.email, "role": "executor"})
        self.assertEqual(response.status_code, 302)
        invitation = ProjectInvitation.objects.get(email=self.outsider.email)
        self.assertContains(self.client.get(url), f"/invitations/{invitation.token}/")
        self.client.force_login(self.foreman)
        self.assertNotContains(self.client.get(url), invitation.token)
        accept_url = reverse("workspace:accept", args=[invitation.token])
        self.assertEqual(self.client.get(accept_url).status_code, 404)
        self.client.force_login(self.outsider)
        self.assertContains(self.client.get(accept_url), "Принять приглашение")
        self.assertRedirects(self.client.post(accept_url), reverse("workspace:stages", args=[self.project.pk]))
        self.assertEqual(self.client.post(accept_url).status_code, 200)
        self.assertEqual(self.project.members.filter(user=self.outsider).count(), 1)

    def test_member_role_change_and_remove(self):
        member = self.project.members.get(user=self.worker)
        url = reverse("workspace:member-action", args=[self.project.pk, member.pk])
        self.assertEqual(self.client.post(url, {"action": "role", "role": "executor"}).status_code, 302)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"action": "remove"}).status_code, 302)
        self.assertFalse(ProjectMember.objects.filter(pk=member.pk).exists())

    def test_invitation_cancel(self):
        self.client.post(reverse("workspace:members", args=[self.project.pk]), {"email": self.outsider.email, "role": "executor"})
        invitation = self.project.invitations.get()
        url = reverse("workspace:invitation-cancel", args=[self.project.pk, invitation.pk])
        self.assertEqual(self.client.post(url).status_code, 302)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "cancelled")

    def test_stage_delete_requires_post_and_own_draft(self):
        url = reverse("workspace:stage-delete", args=[self.project.pk, self.stage.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        self.assertFalse(Stage.objects.filter(pk=self.stage.pk).exists())

    def test_csrf_enforced_for_browser_forms(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        url = reverse("workspace:project-create")
        self.assertEqual(client.post(url, {"name": "Без токена"}).status_code, 403)
        client.get(url)
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post(url, {"name": "С токеном"}, HTTP_X_CSRFTOKEN=token).status_code, 302)

    def test_search_filter_and_html_escaping(self):
        response = self.client.get(reverse("workspace:stages", args=[self.project.pk]), {"q": "не существует"})
        self.assertNotContains(response, "Электрика")
        self.project.name = '<script>alert("x")</script>'
        self.project.save()
        response = self.client.get(reverse("workspace:projects"))
        self.assertNotContains(response, self.project.name)
        self.assertContains(response, "&lt;script&gt;")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class Milestone40PagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from datetime import timedelta
        from django.utils import timezone
        from apps.repairs.services import update_stage
        from apps.repairs.task_services import create_task
        User = get_user_model()
        cls.owner = User.objects.create_user(email="owner40@example.com", full_name="Заказчик", is_email_verified=True)
        cls.foreman = User.objects.create_user(email="foreman40@example.com", full_name="Прораб", is_email_verified=True)
        cls.worker = User.objects.create_user(email="worker40@example.com", full_name="Исполнитель", is_email_verified=True)
        cls.project = create_project(user=cls.owner, name="Квартира", address="ул. Киевская, 1")
        cls.foreman_member = ProjectMember.objects.create(project=cls.project, user=cls.foreman, role="foreman")
        cls.worker_member = ProjectMember.objects.create(project=cls.project, user=cls.worker, role="executor")
        cls.today = timezone.localdate()
        cls.stage = create_stage(project=cls.project, actor=cls.owner, name="Электрика", start_date=cls.today, end_date=cls.today + timedelta(days=5), responsible=cls.foreman_member)
        update_stage(stage=cls.stage, actor=cls.owner, status="in_progress")
        cls.task = create_task(project=cls.project, actor=cls.foreman, stage=cls.stage, name="Розетки", assignee=cls.worker_member, due_date=cls.today)

    def test_pages_render_for_every_role(self):
        pages = [("project", [self.project.pk]), ("tasks", [self.project.pk]), ("task", [self.project.pk, self.task.pk]),
                 ("remarks", [self.project.pk]), ("dashboard", []), ("stages", [self.project.pk])]
        for user in [self.owner, self.foreman, self.worker]:
            self.client.force_login(user)
            for name, args in pages:
                with self.subTest(user=user.email, page=name):
                    self.assertEqual(self.client.get(reverse("workspace:" + name, args=args)).status_code, 200)
        for user, expected in [(self.owner, 200), (self.foreman, 200), (self.worker, 403)]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("workspace:acceptance", args=[self.project.pk])).status_code, expected)

    def test_executor_does_not_see_budget(self):
        self.project.budget = 500000
        self.project.save()
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("workspace:project", args=[self.project.pk])), "500 000,00 сом")
        self.client.force_login(self.worker)
        self.assertNotContains(self.client.get(reverse("workspace:project", args=[self.project.pk])), "500 000")

    def test_project_create_edit_and_delete_draft(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("workspace:project-create"), {"name": "Черновик", "address": "Ахунбаева 5", "area": "54.5", "budget": "300000",
                                                             "start_date": "2026-11-01", "end_date": "2026-10-01"})
        self.assertFalse(Project.objects.filter(name="Черновик").exists())  # dates in wrong order
        self.client.post(reverse("workspace:project-create"), {"name": "Черновик", "area": "54.5"})
        draft = Project.objects.get(name="Черновик")
        response = self.client.post(reverse("workspace:project-edit", args=[draft.pk]), {"name": "Черновик 2", "address": "Ахунбаева 5"})
        self.assertRedirects(response, reverse("workspace:project", args=[draft.pk]))
        draft.refresh_from_db()
        self.assertEqual((draft.name, draft.address), ("Черновик 2", "Ахунбаева 5"))
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse("workspace:project-edit", args=[self.project.pk])).status_code, 403)
        self.client.force_login(self.owner)
        self.client.post(reverse("workspace:project-delete", args=[self.project.pk]))
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())  # has stages
        self.client.post(reverse("workspace:project-delete", args=[draft.pk]))
        self.assertFalse(Project.objects.filter(pk=draft.pk).exists())

    def test_task_form_and_executor_flow_through_pages(self):
        from apps.repairs.models import Task
        self.client.force_login(self.foreman)
        form = self.client.get(reverse("workspace:task-create", args=[self.project.pk])).context["form"]
        payload = {"stage": self.stage.pk, "name": "Свет", "assignee": self.worker_member.pk, "due_date": self.today.isoformat(), "request_id": form.initial["request_id"]}
        self.client.post(reverse("workspace:task-create", args=[self.project.pk]), payload)
        self.client.post(reverse("workspace:task-create", args=[self.project.pk]), payload)  # double click
        self.assertEqual(Task.objects.filter(name="Свет").count(), 1)
        self.client.force_login(self.worker)
        url = reverse("workspace:task-action", args=[self.project.pk, self.task.pk])
        self.client.post(url, {"action": "start", "version": 1})
        self.client.post(url, {"action": "submit", "version": 2, "text": "Установлены"})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "review")
        response = self.client.post(url, {"action": "approve", "version": 3}, follow=True)
        self.assertContains(response, "Проверку выполняют заказчик или прораб")
