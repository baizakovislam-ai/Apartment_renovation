from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.projects.models import Project, ProjectInvitation, ProjectMember, SavedContact
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
        for name, args in [("dashboard", []), ("projects", []), ("project-create", []), ("project-detail", [self.project.pk]), ("project-edit", [self.project.pk]), ("stages", [self.project.pk]), ("members", [self.project.pk]), ("history", [self.project.pk]), ("stage-create", [self.project.pk]), ("stage-edit", [self.project.pk, self.stage.pk])]:
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
        self.assertRedirects(response, reverse("workspace:project-detail", args=[project.pk]))
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
        for name in ["project-detail", "project-edit", "members", "stages", "history", "stage-create"]:
            self.assertEqual(self.client.get(reverse("workspace:" + name, args=[self.project.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("workspace:projects")), self.project.name)

    def test_invitation_full_flow_and_token_privacy(self):
        self.outsider.is_email_verified = False
        self.outsider.save()
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
        self.outsider.is_email_verified = True
        self.outsider.save()
        self.assertRedirects(self.client.post(accept_url), reverse("workspace:project-detail", args=[self.project.pk]))
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
        self.client.post(reverse("workspace:members", args=[self.project.pk]), {"email": "new@example.com", "role": "executor"})
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

    def test_contact_reuse_after_removal_and_in_another_project(self):
        members_url = reverse("workspace:members", args=[self.project.pk])
        response = self.client.post(members_url, {"email": "OTHER@EXAMPLE.COM", "role": "executor"}, follow=True)
        self.assertContains(response, "Участник добавлен в команду")
        member = self.project.members.get(user=self.outsider)
        contact = SavedContact.objects.get(owner=self.owner, email=self.outsider.email)
        self.assertFalse(self.project.invitations.exists())
        self.client.post(reverse("workspace:member-action", args=[self.project.pk, member.pk]), {"action": "remove"})
        self.assertContains(self.client.get(members_url), self.outsider.email)
        other = create_project(user=self.owner, name="Следующий ремонт")
        response = self.client.post(reverse("workspace:members", args=[other.pk]), {"contact": contact.pk, "role": "executor"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(other.members.filter(user=self.outsider).exists())
        self.assertEqual(SavedContact.objects.filter(owner=self.owner, email=self.outsider.email).count(), 1)

    def test_contact_list_is_private_and_rejects_ambiguous_input(self):
        contact = SavedContact.objects.create(owner=self.foreman, email="private@example.com")
        url = reverse("workspace:members", args=[self.project.pk])
        self.assertNotContains(self.client.get(url), contact.email)
        self.assertEqual(self.client.post(url, {"contact": contact.pk, "role": "executor"}).status_code, 200)
        self.assertFalse(self.project.invitations.exists())
        own = SavedContact.objects.create(owner=self.owner, email=self.outsider.email)
        response = self.client.post(url, {"contact": own.pk, "email": "other-person@example.com", "role": "executor"})
        self.assertContains(response, "заполните один вариант")
        self.assertFalse(self.project.members.filter(user=self.outsider).exists())

    def test_planning_does_not_bypass_project_launch(self):
        url = reverse("workspace:stage-quick", args=[self.project.pk])
        response = self.client.post(url, {"name": "Демонтаж"}, follow=True)
        self.assertContains(response, "Демонтаж")
        self.assertContains(response, "Не назначен")
        stage = self.project.stages.get(name="Демонтаж")
        self.assertEqual(stage.position, self.stage.position + 1)
        self.assertIsNone(stage.responsible)
        self.assertIsNone(stage.start_date)
        self.assertEqual(self.client.get("/").status_code, 200)
        action_url = reverse("workspace:stage-action", args=[self.project.pk, stage.pk])
        response = self.client.post(action_url, {"action": "in_progress"}, follow=True)
        self.assertEqual(response.status_code, 404)
        stage.refresh_from_db()
        self.assertEqual(stage.status, "planned")
        data = {**self.stage_payload(), "name": "Демонтаж"}
        self.client.post(reverse("workspace:stage-edit", args=[self.project.pk, stage.pk]), data)
        self.client.post(action_url, {"action": "in_progress"})
        self.client.post(action_url, {"action": "completed"})
        stage.refresh_from_db()
        self.assertEqual(stage.status, "planned")
        self.assertEqual(stage.responsible_id, self.foreman_member.pk)
        response = self.client.get(reverse("workspace:project-detail", args=[self.project.pk]))
        self.assertNotContains(response, "Начать работы")
        self.assertNotContains(response, 'value="completed"')

    def test_customer_foreman_executor_have_distinct_workspaces(self):
        detail = reverse("workspace:project-detail", args=[self.project.pk])
        for user, label, present, absent in [
            (self.owner, "Кабинет заказчика", "Управлять командой", "Добавить этап в план"),
            (self.foreman, "Кабинет прораба", "Добавить этап в план", "Настройки проекта"),
            (self.worker, "Кабинет исполнителя", "План и команда вашего проекта", "Настроить этап"),
        ]:
            with self.subTest(role=label):
                self.client.force_login(user)
                response = self.client.get(detail)
                self.assertContains(response, label)
                self.assertContains(response, present)
                self.assertNotContains(response, absent)
                self.assertContains(self.client.get("/"), label)
        self.client.force_login(self.foreman)
        self.assertNotContains(self.client.get(reverse("workspace:members", args=[self.project.pk])), 'name="contact"')
        self.assertEqual(self.client.post(reverse("workspace:members", args=[self.project.pk]), {"email": self.outsider.email, "role": "executor"}).status_code, 403)
        # FR04 explicitly allows both customer and foreman to plan stages.
        for user in [self.owner, self.foreman]:
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse("workspace:stage-quick", args=[self.project.pk]), {"name": "Планирование"}).status_code, 302)

    def test_foreman_invitation_visible_only_to_recipient_and_requires_acceptance(self):
        project = create_project(user=self.owner, name="Новый ремонт")
        form_page = self.client.get(reverse("workspace:members", args=[project.pk]), {"role": "foreman"})
        self.assertEqual(form_page.context["form"]["role"].value(), "foreman")
        self.client.post(reverse("workspace:members", args=[project.pk]), {"email": self.outsider.email, "role": "foreman"})
        invitation = project.invitations.get()
        self.assertFalse(project.members.filter(user=self.outsider).exists())
        self.client.force_login(self.worker)
        self.assertNotContains(self.client.get(reverse("workspace:projects")), invitation.token)
        self.client.force_login(self.outsider)
        self.assertContains(self.client.get(reverse("workspace:projects")), "Рассмотреть приглашение")
        self.assertEqual(self.client.get(reverse("workspace:project-detail", args=[project.pk])).status_code, 404)
        response = self.client.post(reverse("workspace:accept", args=[invitation.token]), follow=True)
        self.assertContains(response, "Кабинет прораба")
        self.assertNotContains(self.client.get(reverse("workspace:projects")), invitation.token)

    def test_role_is_per_project_not_per_account(self):
        self.client.force_login(self.foreman)
        response = self.client.post(reverse("workspace:project-create"), {"name": "Собственная квартира"}, follow=True)
        self.assertContains(response, "Кабинет заказчика")
        self.assertContains(self.client.get(reverse("workspace:project-detail", args=[self.project.pk])), "Кабинет прораба")

    def test_stage_reordering_and_project_isolation(self):
        other_stage = create_stage(project=self.project, actor=self.owner, name="Отделка")
        action_url = reverse("workspace:stage-action", args=[self.project.pk, other_stage.pk])
        self.assertEqual(self.client.get(action_url).status_code, 405)
        self.client.post(action_url, {"action": "up"})
        self.assertEqual(list(self.project.stages.values_list("name", flat=True)), ["Отделка", "Электрика"])
        self.assertTrue(self.project.events.filter(action="stage.reordered").exists())
        other_project = create_project(user=self.owner, name="Второй объект")
        foreign_url = reverse("workspace:stage-action", args=[other_project.pk, other_stage.pk])
        self.assertEqual(self.client.post(foreign_url, {"action": "down"}).status_code, 404)
        self.client.force_login(self.worker)
        self.client.post(action_url, {"action": "down"})
        self.assertEqual(self.project.stages.first(), other_stage)
        self.assertEqual(self.client.post(reverse("workspace:stage-quick", args=[self.project.pk]), {"name": "Подмена"}).status_code, 403)

    def test_project_settings_and_cross_project_navigation(self):
        self.assertRedirects(self.client.get("/", {"project": self.project.pk}), reverse("workspace:project-detail", args=[self.project.pk]))
        url = reverse("workspace:project-edit", args=[self.project.pk])
        response = self.client.post(url, {"name": "Новое имя", "address": "Бишкек", "description": "Ремонт кухни"}, follow=True)
        self.assertContains(response, "Бишкек")
        self.assertContains(response, "Ремонт кухни")
        self.assertContains(response, reverse("workspace:members", args=[self.project.pk]))
        self.client.force_login(self.foreman)
        self.assertEqual(self.client.post(url, {"name": "Подмена"}).status_code, 403)
