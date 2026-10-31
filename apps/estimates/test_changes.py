"""Abdulazis's 60 % part: changes of the approved estimate (ТЗ FR11)."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.projects.models import ProjectEvent, ProjectMember
from apps.projects.services import create_project
from apps.repairs.models import Stage
from apps.repairs.services import create_stage
from . import change_services as changes
from . import search, services
from .models import ChangeRequest, Estimate


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ChangeTestBase(TestCase):
    def setUp(self):
        User = get_user_model()
        make = lambda email: User.objects.create_user(email=email, full_name=email.split("@")[0].title(), is_email_verified=True)
        self.customer, self.foreman, self.worker, self.stranger = map(make, ["customer@example.com", "foreman@example.com",
                                                                             "worker@example.com", "stranger@example.com"])
        self.project = create_project(user=self.customer, name="Квартира", budget=Decimal("50000"), area=Decimal("40"))
        self.foreman_member = ProjectMember.objects.create(project=self.project, user=self.foreman, role="foreman")
        ProjectMember.objects.create(project=self.project, user=self.worker, role="executor")
        self.today = timezone.localdate()
        self.tiles = create_stage(project=self.project, actor=self.foreman, name="Плитка", start_date=self.today,
                                  end_date=self.today + timedelta(days=10), responsible=self.foreman_member)
        self.electric = create_stage(project=self.project, actor=self.foreman, name="Электрика", start_date=self.today,
                                     end_date=self.today + timedelta(days=5), responsible=self.foreman_member)
        self.base = self.approved_estimate()
        self.tile_line = self.base.lines.get(name="Укладка плитки")
        self.glue_line = self.base.lines.get(name="Плиточный клей")
        self.cable_line = self.base.lines.get(name="Кабель")

    def approved_estimate(self):
        estimate = services.create_draft(project=self.project, actor=self.foreman)
        for stage, name, cost_type, unit, quantity, price in [
            (self.tiles, "Укладка плитки", "work", "м²", "20", "900"),       # 18 000
            (self.tiles, "Плиточный клей", "material", "мешок", "10", "500"),  # 5 000
            (self.electric, "Кабель", "material", "м", "100", "80"),           # 8 000
        ]:
            estimate.refresh_from_db()
            services.add_line(estimate=estimate, actor=self.foreman, revision=estimate.revision, stage=stage, name=name,
                              cost_type=cost_type, unit=unit, quantity=quantity, price=price)
        estimate.refresh_from_db()
        services.submit_estimate(estimate=estimate, actor=self.foreman)
        return services.approve_estimate(estimate=estimate, actor=self.customer)

    def new_request(self, actor=None, **extra):
        return changes.create_request(project=self.project, actor=actor or self.foreman, reason="Заказчик выбрал другую плитку", **extra)

    def set(self, change, actor=None, **data):
        change.refresh_from_db()
        return changes.set_item(request=change, actor=actor or self.foreman, revision=change.revision, **data)

    def submit(self, change, actor=None):
        change.refresh_from_db()
        return changes.submit_request(request=change, actor=actor or self.foreman, revision=change.revision)


class ChangeFlowTests(ChangeTestBase):
    def test_full_cycle_builds_new_approved_version(self):
        new_end = self.today + timedelta(days=14)
        change = self.new_request(schedule_stage=self.tiles, new_end_date=new_end)
        self.assertEqual((change.status, change.number, change.estimate), ("draft", 1, self.base))
        self.set(change, action="update", line=self.tile_line, price="1100")              # +4 000
        self.set(change, action="remove", line=self.glue_line)                            # −5 000
        self.set(change, action="add", stage=self.tiles, name="Клей эластичный", cost_type="material",
                 unit="мешок", quantity="12", price="650")                                  # +7 800
        change.refresh_from_db()
        self.assertEqual(change.cost_delta, Decimal("6800.00"))
        self.assertEqual(change.new_total, Decimal("37800.00"))  # 31 000 + 6 800
        self.assertEqual(change.days_delta, 4)
        effect = changes.budget_effect(change)
        self.assertEqual((effect["left_before"], effect["left_after"], effect["over_budget"]), (Decimal("19000.00"), Decimal("12200.00"), False))
        update = change.items.get(action="update")
        self.assertEqual(update.changed_fields, [("Цена", Decimal("900.00"), Decimal("1100.00"))])

        self.submit(change)
        change.refresh_from_db()
        self.assertEqual((change.status, change.submitted_by), ("pending", self.foreman))
        with self.assertRaises(ValidationError):  # a pending request is locked
            self.set(change, action="update", line=self.cable_line, price="90")
        with self.assertRaises(PermissionDenied):  # the foreman never approves (ТЗ 4.1.1)
            changes.approve_request(request=change, actor=self.foreman)

        changes.approve_request(request=change, actor=self.customer)
        change.refresh_from_db()
        self.base.refresh_from_db()
        new = services.approved_estimate(self.project)
        self.assertEqual((change.status, change.result_estimate, change.decided_by), ("approved", new, self.customer))
        self.assertEqual((new.number, new.based_on, new.total, new.status), (2, self.base, Decimal("37800.00"), "approved"))
        self.assertEqual(self.base.status, "superseded")
        self.assertEqual(sorted(new.lines.values_list("name", "price")),
                         [("Кабель", Decimal("80.00")), ("Клей эластичный", Decimal("650.00")), ("Укладка плитки", Decimal("1100.00"))])
        self.assertEqual(self.base.lines.count(), 3)  # the approved data itself is never edited (ТЗ 4.12)
        self.tiles.refresh_from_db()
        self.assertEqual((self.tiles.end_date, change.old_end_date), (new_end, self.today + timedelta(days=10)))
        self.assertEqual(change.days_delta, 4)  # still shown correctly after the stage moved
        with self.assertRaises(ValidationError):  # a repeated click does nothing twice (ТЗ 4.13)
            changes.approve_request(request=change, actor=self.customer)
        self.assertEqual(Estimate.objects.filter(project=self.project).count(), 2)
        actions = set(ProjectEvent.objects.values_list("action", flat=True))
        self.assertTrue({"estimate_change.created", "estimate_change.item_set", "estimate_change.submitted",
                         "estimate_change.approved", "stage.updated"} <= actions)

    def test_customer_can_propose_and_one_line_is_changed_once(self):
        change = self.new_request(actor=self.customer)
        self.set(change, actor=self.customer, action="update", line=self.cable_line, quantity="120")
        self.set(change, actor=self.customer, action="update", line=self.cable_line, quantity="150")  # replaces, not duplicates
        self.assertEqual(change.items.count(), 1)
        self.assertEqual(change.items.get().quantity, Decimal("150"))
        self.set(change, actor=self.customer, action="remove", line=self.cable_line)
        self.assertEqual(change.items.get().action, "remove")
        item = change.items.get()
        change.refresh_from_db()
        changes.delete_item(item=item, actor=self.customer, revision=change.revision)
        self.assertFalse(change.items.exists())

    def test_reject_needs_reason_and_is_final(self):
        change = self.new_request()
        self.set(change, action="update", line=self.cable_line, price="100")
        self.submit(change)
        with self.assertRaises(ValidationError):
            changes.reject_request(request=change, actor=self.customer, reason="  ")
        changes.reject_request(request=change, actor=self.customer, reason="Дорого, оставьте старый кабель")
        change.refresh_from_db()
        self.assertEqual((change.status, change.decision_reason), ("rejected", "Дорого, оставьте старый кабель"))
        with self.assertRaises(ValidationError):
            changes.approve_request(request=change, actor=self.customer)
        self.assertEqual(services.approved_estimate(self.project), self.base)  # nothing changed

    def test_withdraw_and_delete_only_by_author(self):
        draft = self.new_request()
        with self.assertRaises(PermissionDenied):
            changes.delete_request(request=draft, actor=self.customer)
        with self.assertRaises(PermissionDenied):
            self.set(draft, actor=self.customer, action="remove", line=self.cable_line)
        changes.delete_request(request=draft, actor=self.foreman)
        self.assertFalse(ChangeRequest.objects.filter(pk=draft.pk).exists())

        change = self.new_request()
        self.set(change, action="remove", line=self.cable_line)
        with self.assertRaises(ValidationError):  # a draft is deleted, not withdrawn
            changes.withdraw_request(request=change, actor=self.foreman)
        self.submit(change)
        with self.assertRaises(ValidationError):  # a submitted request is withdrawn, not deleted (ТЗ 4.12)
            changes.delete_request(request=change, actor=self.foreman)
        with self.assertRaises(PermissionDenied):
            changes.withdraw_request(request=change, actor=self.customer)
        changes.withdraw_request(request=change, actor=self.foreman)
        change.refresh_from_db()
        self.assertEqual(change.status, "withdrawn")


class ChangeRulesTests(ChangeTestBase):
    def test_validation(self):
        with self.assertRaises(ValidationError):  # the reason is required
            changes.create_request(project=self.project, actor=self.foreman, reason=" ")
        change = self.new_request()
        with self.assertRaises(ValidationError):  # nothing changed
            self.set(change, action="update", line=self.cable_line, price="80")
        for bad in [{"quantity": "0"}, {"price": "-1"}, {"quantity": "много"}, {"name": " "}]:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.set(change, action="update", line=self.cable_line, **bad)
        with self.assertRaises(ValidationError):  # a new position needs all values
            self.set(change, action="add", stage=self.tiles, name="Затирка")
        with self.assertRaises(ValidationError):
            self.set(change, action="remove", line=None)
        with self.assertRaises(ValidationError):  # empty request cannot be sent
            self.submit(change)
        other = create_project(user=self.stranger, name="Чужой")
        other_stage = create_stage(project=other, actor=self.stranger, name="Чужой", start_date=self.today, end_date=self.today,
                                   responsible=other.members.get())
        with self.assertRaises(ValidationError):
            self.set(change, action="add", stage=other_stage, name="Чужое", unit="шт", quantity="1", price="1")
        for stage, end in [(self.tiles, self.today - timedelta(days=1)), (self.tiles, self.tiles.end_date), (other_stage, self.today), (self.tiles, None)]:
            with self.subTest(stage=stage.name, end=end), self.assertRaises(ValidationError):
                changes.create_request(project=self.project, actor=self.foreman, reason="Сроки", schedule_stage=stage, new_end_date=end)
        Stage.objects.filter(pk=self.electric.pk).update(status="accepted")
        self.electric.refresh_from_db()
        with self.assertRaises(ValidationError):  # an accepted stage keeps its deadline
            changes.create_request(project=self.project, actor=self.foreman, reason="Сроки", schedule_stage=self.electric,
                                   new_end_date=self.today + timedelta(days=30))

    def test_schedule_only_request(self):
        change = self.new_request(schedule_stage=self.electric, new_end_date=self.today + timedelta(days=8))
        self.submit(change)
        changes.approve_request(request=change, actor=self.customer)
        self.electric.refresh_from_db()
        self.assertEqual(self.electric.end_date, self.today + timedelta(days=8))
        self.assertEqual(services.approved_estimate(self.project).total, self.base.total)

    def test_needs_approved_estimate(self):
        project = create_project(user=self.customer, name="Без сметы")
        with self.assertRaises(ValidationError):
            changes.create_request(project=project, actor=self.customer, reason="Хочу дешевле")

    def test_line_of_other_version_is_refused(self):
        change = self.new_request()
        draft = services.create_draft(project=self.project, actor=self.foreman)
        with self.assertRaises(ValidationError):
            self.set(change, action="remove", line=draft.lines.first())

    def test_stale_request_cannot_be_applied(self):
        first, second = self.new_request(), self.new_request()
        self.set(first, action="update", line=self.cable_line, price="90")
        self.set(second, action="update", line=self.tile_line, price="950")
        self.submit(first)
        self.submit(second)
        changes.approve_request(request=first, actor=self.customer)
        with self.assertRaises(ValidationError):  # second was made for v1, the estimate is v2 now
            changes.approve_request(request=second, actor=self.customer)
        changes.reject_request(request=second, actor=self.customer, reason="Устарела")
        third = self.new_request()
        self.assertEqual(third.estimate, services.approved_estimate(self.project))
        with self.assertRaises(ValidationError):  # a v1 line, but the third request is made for v2
            self.set(third, action="remove", line=self.cable_line)

    def test_open_new_version_blocks_approval(self):
        change = self.new_request()
        self.set(change, action="remove", line=self.cable_line)
        self.submit(change)
        services.create_draft(project=self.project, actor=self.foreman)
        with self.assertRaises(ValidationError):
            changes.approve_request(request=change, actor=self.customer)

    def test_stale_revision(self):
        change = self.new_request()
        old = change.revision
        self.set(change, action="remove", line=self.cable_line)
        with self.assertRaises(ValidationError):
            changes.set_item(request=change, actor=self.foreman, revision=old, action="remove", line=self.glue_line)

    def test_executor_and_stranger_have_no_access(self):
        with self.assertRaises(PermissionDenied):
            self.new_request(actor=self.worker)
        change = self.new_request()
        self.set(change, action="remove", line=self.cable_line)
        self.submit(change)
        with self.assertRaises(PermissionDenied):
            changes.approve_request(request=change, actor=self.worker)
        client = APIClient()
        client.force_authenticate(self.worker)
        self.assertEqual(client.get(f"/api/projects/{self.project.pk}/estimate-changes/").status_code, 403)
        self.assertEqual(client.get(f"/api/estimate-changes/{change.pk}/").status_code, 403)
        client.force_authenticate(self.stranger)
        self.assertEqual(client.get(f"/api/estimate-changes/{change.pk}/").status_code, 404)
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse("workspace:estimate-changes", args=[self.project.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("workspace:estimate-change", args=[self.project.pk, change.pk])).status_code, 403)
        self.assertEqual(search.approval_items(self.project, self.worker, {}), [])

    def test_approvals_journal_lists_changes(self):
        change = self.new_request()
        self.set(change, action="update", line=self.cable_line, price="100")
        self.submit(change)
        items = search.approval_items(self.project, self.customer, {"type": "change"})
        self.assertEqual([(item["title"], item["status"], item["amount"]) for item in items], [("Изменение сметы №1", "pending", Decimal("2000.00"))])


class ChangeApiTests(ChangeTestBase):
    def test_api_flow(self):
        api = APIClient()
        api.force_authenticate(self.foreman)
        response = api.post(f"/api/projects/{self.project.pk}/estimate-changes/", {"reason": "Подорожал кабель"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        change_id, revision = response.data["id"], response.data["revision"]
        response = api.post(f"/api/estimate-changes/{change_id}/items/", {"action": "update", "line": self.cable_line.pk, "price": "95",
                                                                         "revision": revision}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(response.data["cost_delta"]), Decimal("1500.00"))
        response = api.post(f"/api/estimate-changes/{change_id}/action/", {"action": "submit", "revision": response.data["revision"]}, format="json")
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(api.post(f"/api/estimate-changes/{change_id}/action/", {"action": "approve"}, format="json").status_code, 403)
        api.force_authenticate(self.customer)
        response = api.post(f"/api/estimate-changes/{change_id}/action/", {"action": "approve"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["status"], Decimal(response.data["new_total"])), ("approved", Decimal("32500.00")))
        self.assertEqual(services.approved_estimate(self.project).total, Decimal("32500.00"))


class ChangePagesTests(ChangeTestBase):
    def url(self, name, *args):
        return reverse(f"workspace:{name}", args=[self.project.pk, *args])

    def test_pages_flow(self):
        self.client.force_login(self.foreman)
        self.assertContains(self.client.get(self.url("estimate")), "Предложить изменение")
        response = self.client.post(self.url("estimate-changes"), {"reason": "Нашли скрытую проводку", "schedule_stage": self.electric.pk,
                                                                   "new_end_date": (self.today + timedelta(days=9)).isoformat()})
        change = ChangeRequest.objects.get()
        self.assertRedirects(response, self.url("estimate-change", change.pk))
        page = self.client.get(self.url("estimate-change", change.pk))
        self.assertContains(page, "Нашли скрытую проводку")
        self.assertContains(page, "+4 дн.")
        response = self.client.post(self.url("estimate-change-line", change.pk, self.cable_line.pk),
                                    {"stage": self.electric.pk, "name": "Кабель", "cost_type": "material", "unit": "м",
                                     "quantity": "150", "price": "80", "revision": change.revision})
        self.assertRedirects(response, self.url("estimate-change", change.pk) + "#changes", fetch_redirect_response=False)
        change.refresh_from_db()
        action = self.url("estimate-change-action", change.pk)
        self.client.post(action, {"action": "add_item", "stage": self.electric.pk, "name": "Автомат 16А", "cost_type": "equipment",
                                  "unit": "шт", "quantity": "2", "price": "750", "revision": change.revision})
        change.refresh_from_db()
        page = self.client.get(self.url("estimate-change", change.pk))
        self.assertContains(page, "+5 500,00 сом")  # +4 000 cable + 1 500 breakers
        self.assertContains(page, "Автомат 16А")
        self.client.post(action, {"action": "submit", "revision": change.revision})
        change.refresh_from_db()
        self.assertEqual(change.status, "pending")

        self.client.force_login(self.customer)
        dashboard = self.client.get(reverse("workspace:dashboard") + f"?project={self.project.pk}")
        self.assertContains(dashboard, "Изменение сметы №1 ждёт вашего решения")
        page = self.client.get(self.url("estimate-change", change.pk))
        self.assertContains(page, "Одобрить изменение сметы на +5 500,00 сом?")
        self.client.post(action, {"action": "approve"})
        change.refresh_from_db()
        self.assertEqual(change.status, "approved")
        estimate_page = self.client.get(self.url("estimate"))
        self.assertContains(estimate_page, "36 500,00 сом")
        self.assertContains(estimate_page, "заявке на изменение №1")
        self.assertContains(self.client.get(self.url("approvals")), "Изменение сметы №1")
        self.assertContains(self.client.get(self.url("estimate-changes")), "Одобрена")

    def test_list_without_approved_estimate(self):
        project = create_project(user=self.customer, name="Пустой")
        self.client.force_login(self.customer)
        page = self.client.get(reverse("workspace:estimate-changes", args=[project.pk]))
        self.assertContains(page, "Сначала нужна утверждённая смета")
