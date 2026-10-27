"""Islam's 60–80 % part: estimate and its approval, works start, preliminary calculation, search and filters."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.projects.models import Project, ProjectEvent, ProjectMember
from apps.projects.services import create_project, update_project
from apps.repairs.services import create_stage, update_stage
from apps.repairs.task_services import create_task
from . import search, services
from .models import Estimate, EstimateLine


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class EstimateFlowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        make = lambda email: User.objects.create_user(email=email, full_name=email.split("@")[0].title(), is_email_verified=True)
        self.customer, self.foreman, self.worker, self.stranger = map(make, ["customer@example.com", "foreman@example.com",
                                                                             "worker@example.com", "stranger@example.com"])
        self.project = create_project(user=self.customer, name="Квартира", budget=Decimal("100000"), area=Decimal("50"))
        self.foreman_member = ProjectMember.objects.create(project=self.project, user=self.foreman, role="foreman")
        self.worker_member = ProjectMember.objects.create(project=self.project, user=self.worker, role="executor")
        self.today = timezone.localdate()
        self.stage = create_stage(project=self.project, actor=self.foreman, name="Плитка", start_date=self.today,
                                  end_date=self.today + timedelta(days=10), responsible=self.foreman_member)

    def add(self, estimate, actor=None, **data):
        estimate.refresh_from_db()
        values = {"stage": self.stage, "name": "Укладка плитки", "cost_type": "work", "unit": "м²", "quantity": "20", "price": "900", **data}
        return services.add_line(estimate=estimate, actor=actor or self.foreman, revision=estimate.revision, **values)

    def approved(self):
        estimate = services.create_draft(project=self.project, actor=self.foreman)
        self.add(estimate)
        estimate.refresh_from_db()
        services.submit_estimate(estimate=estimate, actor=self.foreman)
        return services.approve_estimate(estimate=estimate, actor=self.customer)

    # --- FR09: create and edit ---

    def test_lines_are_calculated_and_total_updates(self):
        estimate = services.create_draft(project=self.project, actor=self.foreman)
        line = self.add(estimate)
        self.add(estimate, actor=self.customer, name="Плиточный клей", cost_type="material", unit="мешок", quantity="12,5", price="480.40")
        estimate.refresh_from_db()
        self.assertEqual(line.amount, Decimal("18000.00"))
        self.assertEqual(estimate.total, Decimal("24005.00"))  # 18000 + 12.5 × 480.40
        line.refresh_from_db()
        estimate.refresh_from_db()
        services.update_line(line=line, actor=self.foreman, revision=estimate.revision, quantity="25")
        estimate.refresh_from_db()
        self.assertEqual(estimate.total, Decimal("28505.00"))
        services.delete_line(line=line, actor=self.foreman, revision=estimate.revision)
        estimate.refresh_from_db()
        self.assertEqual(estimate.total, Decimal("6005.00"))
        self.assertTrue(ProjectEvent.objects.filter(action="estimate.line_deleted").exists())

    def test_line_validation(self):
        estimate = services.create_draft(project=self.project, actor=self.foreman)
        for bad in [{"quantity": "0"}, {"quantity": "-1"}, {"price": "-5"}, {"name": "  "}, {"unit": ""}, {"quantity": "много"}]:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.add(estimate, **bad)
        other = create_project(user=self.stranger, name="Чужой")
        other_stage = create_stage(project=other, actor=self.stranger, name="Чужой", start_date=self.today, end_date=self.today, responsible=other.members.get())
        with self.assertRaises(ValidationError):
            self.add(estimate, stage=other_stage)
        self.assertFalse(EstimateLine.objects.exists())

    def test_stale_revision_is_rejected(self):
        estimate = services.create_draft(project=self.project, actor=self.foreman)
        old_revision = estimate.revision
        self.add(estimate)
        with self.assertRaises(ValidationError):
            services.add_line(estimate=estimate, actor=self.customer, revision=old_revision, stage=self.stage, name="Ещё", unit="шт", quantity="1", price="1")

    def test_executor_and_stranger_have_no_access(self):
        estimate = services.create_draft(project=self.project, actor=self.foreman)
        with self.assertRaises(PermissionDenied):
            self.add(estimate, actor=self.worker)
        with self.assertRaises(PermissionDenied):
            services.create_calculation(project=self.project, actor=self.worker, name="Мой")
        client = APIClient()
        client.force_authenticate(self.worker)
        self.assertEqual(client.get(f"/api/projects/{self.project.pk}/estimates/").status_code, 403)
        self.assertEqual(client.get(f"/api/estimates/{estimate.pk}/").status_code, 403)
        client.force_authenticate(self.stranger)
        self.assertEqual(client.get(f"/api/estimates/{estimate.pk}/").status_code, 404)
        self.client.force_login(self.worker)
        for name in ["estimate", "calculations", "approvals"]:
            self.assertEqual(self.client.get(reverse(f"workspace:{name}", args=[self.project.pk])).status_code, 403)
        page = self.client.get(reverse("workspace:project", args=[self.project.pk]))
        self.assertNotContains(page, reverse("workspace:estimate", args=[self.project.pk]))  # no estimate link in the menu
        self.assertNotContains(page, "Лимит бюджета")

    def test_only_one_open_version(self):
        services.create_draft(project=self.project, actor=self.foreman)
        with self.assertRaises(ValidationError):
            services.create_draft(project=self.project, actor=self.customer)

    # --- FR10: approval ---

    def test_full_approval_cycle_with_rejection_and_new_version(self):
        estimate = services.create_draft(project=self.project, actor=self.foreman)
        with self.assertRaises(ValidationError):  # empty estimate cannot be sent
            services.submit_estimate(estimate=estimate, actor=self.foreman)
        self.add(estimate)
        estimate.refresh_from_db()
        with self.assertRaises(PermissionDenied):  # only the foreman sends for approval
            services.submit_estimate(estimate=estimate, actor=self.customer)
        services.submit_estimate(estimate=estimate, actor=self.foreman, comment="Цены на неделю")
        estimate.refresh_from_db()
        self.assertEqual((estimate.status, estimate.submitted_by), ("pending", self.foreman))
        with self.assertRaises(ValidationError):  # pending estimate is locked
            self.add(estimate)
        with self.assertRaises(PermissionDenied):  # the foreman cannot approve his own request
            services.approve_estimate(estimate=estimate, actor=self.foreman)
        with self.assertRaises(ValidationError):
            services.reject_estimate(estimate=estimate, actor=self.customer, reason=" ")
        services.reject_estimate(estimate=estimate, actor=self.customer, reason="Дорогая плитка")
        estimate.refresh_from_db()
        self.assertEqual((estimate.status, estimate.decision_reason, estimate.decided_by), ("rejected", "Дорогая плитка", self.customer))
        second = services.create_draft(project=self.project, actor=self.foreman)
        self.assertEqual((second.number, second.based_on, second.lines.count()), (2, estimate, 1))  # lines copied
        line = second.lines.get()
        second.refresh_from_db()
        services.update_line(line=line, actor=self.foreman, revision=second.revision, price="700")
        second.refresh_from_db()
        services.submit_estimate(estimate=second, actor=self.foreman)
        services.approve_estimate(estimate=second, actor=self.customer)
        second.refresh_from_db()
        self.assertEqual((second.status, second.total), ("approved", Decimal("14000.00")))
        with self.assertRaises(ValidationError):  # repeated click
            services.approve_estimate(estimate=second, actor=self.customer)
        third = services.create_draft(project=self.project, actor=self.foreman)
        self.add(third)
        third.refresh_from_db()
        services.submit_estimate(estimate=third, actor=self.foreman)
        services.approve_estimate(estimate=third, actor=self.customer)
        second.refresh_from_db()
        self.assertEqual(second.status, "superseded")
        self.assertEqual(services.approved_estimate(self.project), third)

    def test_withdraw_returns_to_draft(self):
        estimate = services.create_draft(project=self.project, actor=self.foreman)
        self.add(estimate)
        estimate.refresh_from_db()
        services.submit_estimate(estimate=estimate, actor=self.foreman)
        services.withdraw_estimate(estimate=estimate, actor=self.foreman)
        estimate.refresh_from_db()
        self.assertEqual(estimate.status, "draft")

    # --- scenario 4.2: works start ---

    def test_works_start_only_after_approval_and_stage_waits_for_it(self):
        with self.assertRaises(ValidationError):
            update_stage(stage=self.stage, actor=self.foreman, status="in_progress")
        self.assertIn("смета ещё не утверждена", services.start_blockers(self.project))
        with self.assertRaises(ValidationError):
            services.start_works(project=self.project, actor=self.customer)
        self.approved()
        with self.assertRaises(PermissionDenied):
            services.start_works(project=self.project, actor=self.foreman)
        services.start_works(project=self.project, actor=self.customer)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, "in_progress")
        update_stage(stage=self.stage, actor=self.foreman, status="in_progress")
        with self.assertRaises(ValidationError):
            services.start_works(project=self.project, actor=self.customer)

    def test_stage_with_estimate_lines_cannot_be_deleted(self):
        from apps.repairs.services import delete_stage
        estimate = services.create_draft(project=self.project, actor=self.foreman)
        self.add(estimate)
        with self.assertRaises(ValidationError):
            delete_stage(stage=self.stage, actor=self.foreman)

    # --- FR16: preliminary calculation ---

    def test_calculation_totals_and_transfer_to_estimate(self):
        calculation = services.create_calculation(project=self.project, actor=self.customer, name="Эконом")
        services.add_item(calculation=calculation, actor=self.customer, work="Штукатурка", unit="м²", volume="50", price="450")
        services.add_item(calculation=calculation, actor=self.foreman, work="Ламинат", cost_type="material", unit="м²", volume="48,5", price="1450")
        self.assertEqual(calculation.total, Decimal("92825.00"))  # 22 500 + 70 325
        with self.assertRaises(ValidationError):
            services.add_item(calculation=calculation, actor=self.customer, work="Ноль", unit="м²", volume="0", price="1")
        with self.assertRaises(ValidationError):
            services.calculation_to_estimate(calculation=calculation, actor=self.customer, stage=None)
        estimate = services.calculation_to_estimate(calculation=calculation, actor=self.customer, stage=self.stage)
        self.assertEqual((estimate.status, estimate.lines.count(), estimate.total), ("draft", 2, Decimal("92825.00")))
        services.calculation_to_estimate(calculation=calculation, actor=self.customer, stage=self.stage)  # appended to the same draft
        estimate.refresh_from_db()
        self.assertEqual(estimate.lines.count(), 4)
        services.delete_calculation(calculation=calculation, actor=self.foreman)
        self.assertEqual(estimate.lines.count(), 4)  # the estimate keeps its lines

    def test_transfer_refused_while_estimate_is_pending(self):
        estimate = services.create_draft(project=self.project, actor=self.foreman)
        self.add(estimate)
        estimate.refresh_from_db()
        services.submit_estimate(estimate=estimate, actor=self.foreman)
        calculation = services.create_calculation(project=self.project, actor=self.customer, name="Вариант")
        services.add_item(calculation=calculation, actor=self.customer, work="Покраска", unit="м²", volume="10", price="300")
        with self.assertRaises(ValidationError):
            services.calculation_to_estimate(calculation=calculation, actor=self.customer, stage=self.stage)

    # --- FR17: search and filters ---

    def test_approvals_journal_filters(self):
        self.approved()
        rejected = services.create_draft(project=self.project, actor=self.foreman)
        rejected.refresh_from_db()
        services.submit_estimate(estimate=rejected, actor=self.foreman)
        services.reject_estimate(estimate=rejected, actor=self.customer, reason="Нужен второй вариант плитки")
        items = search.approval_items(self.project, self.customer, {})
        self.assertEqual([item["status"] for item in items], ["rejected", "approved"])
        self.assertEqual(len(search.approval_items(self.project, self.customer, {"status": "approved"})), 1)
        self.assertEqual(len(search.approval_items(self.project, self.customer, {"q": "второй вариант"})), 1)
        self.assertEqual(len(search.approval_items(self.project, self.customer, {"type": "acceptance"})), 0)
        tomorrow = (self.today + timedelta(days=1)).isoformat()
        self.assertEqual(len(search.approval_items(self.project, self.customer, {"date_from": tomorrow})), 0)
        self.assertEqual(search.approval_items(self.project, self.worker, {}), [])

    def test_project_search_respects_roles(self):
        estimate = services.create_draft(project=self.project, actor=self.foreman)
        self.add(estimate, name="Плитка напольная керамогранит")
        from apps.estimates.testing import mark_started
        mark_started(self.project)
        update_stage(stage=self.stage, actor=self.foreman, status="in_progress")
        other_worker = get_user_model().objects.create_user(email="w2@example.com", full_name="Второй", is_email_verified=True)
        other_member = ProjectMember.objects.create(project=self.project, user=other_worker, role="executor")
        create_task(project=self.project, actor=self.foreman, stage=self.stage, name="Керамогранит в ванной", assignee=self.worker_member, due_date=self.today)
        create_task(project=self.project, actor=self.foreman, stage=self.stage, name="Керамогранит на кухне", assignee=other_member, due_date=self.today)
        found = search.project_search(self.project, self.customer, "керамогранит")
        self.assertEqual((len(found["tasks"]), len(found["estimate_lines"])), (2, 1))
        found = search.project_search(self.project, self.worker, "керамогранит")
        self.assertEqual((len(found["tasks"]), len(found["estimate_lines"])), (1, 0))  # own task only, no estimate
        self.assertIsNone(search.project_search(self.project, self.worker, "к"))

    def test_search_finds_other_word_forms_and_all_words(self):
        estimate = services.create_draft(project=self.project, actor=self.foreman)
        self.add(estimate, name="Прокладка кабеля в коридоре")
        self.add(estimate, name="Плитка для ванной")
        self.assertEqual(len(search.project_search(self.project, self.customer, "кабель")["estimate_lines"]), 1)
        self.assertEqual(len(search.project_search(self.project, self.customer, "плитки ванная")["estimate_lines"]), 1)
        self.assertEqual(len(search.project_search(self.project, self.customer, "кабель ванная")["estimate_lines"]), 0)

    def test_task_filters_by_assignee_dates_and_sort(self):
        from apps.estimates.testing import mark_started
        from apps.repairs.views import filter_tasks
        mark_started(self.project)
        first = create_task(project=self.project, actor=self.foreman, stage=self.stage, name="Б-задача", assignee=self.worker_member, due_date=self.today)
        second = create_task(project=self.project, actor=self.foreman, stage=self.stage, name="А-задача", assignee=self.worker_member, due_date=self.today + timedelta(days=5))
        tasks = self.project.tasks.all()
        self.assertEqual(list(filter_tasks(tasks, {"due_from": (self.today + timedelta(days=1)).isoformat()})), [second])
        self.assertEqual(list(filter_tasks(tasks, {"due_to": self.today.isoformat()})), [first])
        self.assertEqual(list(filter_tasks(tasks, {"deadline": "soon"})), [first])
        self.assertEqual(list(filter_tasks(tasks, {"sort": "name"})), [second, first])
        self.assertEqual(list(filter_tasks(tasks, {"sort": "-due"})), [second, first])
        self.assertEqual(list(filter_tasks(tasks, {"due_from": "кривая дата"})), [first, second])


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class EstimatePagesTests(TestCase):
    """The same flow through the real pages, as people click it."""

    def setUp(self):
        User = get_user_model()
        make = lambda email, name: User.objects.create_user(email=email, full_name=name, is_email_verified=True)
        self.customer, self.foreman = make("c@example.com", "Заказчик"), make("f@example.com", "Прораб")
        self.project = create_project(user=self.customer, name="Квартира", budget=Decimal("20000"))
        self.foreman_member = ProjectMember.objects.create(project=self.project, user=self.foreman, role="foreman")
        today = timezone.localdate()
        self.stage = create_stage(project=self.project, actor=self.foreman, name="Электрика", start_date=today,
                                  end_date=today + timedelta(days=5), responsible=self.foreman_member)

    def url(self, name, *args):
        return reverse(f"workspace:{name}", args=[self.project.pk, *args])

    def test_pages_flow(self):
        self.client.force_login(self.foreman)
        self.assertContains(self.client.get(self.url("estimate")), "Сметы ещё нет")
        self.client.post(self.url("estimate-create"))
        estimate = Estimate.objects.get()
        line = {"stage": self.stage.pk, "name": "Кабель", "cost_type": "material", "unit": "м", "quantity": "100", "price": "250", "revision": estimate.revision}
        self.client.post(self.url("estimate-line-add", estimate.pk), line)
        page = self.client.get(self.url("estimate"))
        self.assertContains(page, "25 000,00 сом")
        self.assertContains(page, "Превышение лимита")  # 25 000 > budget 20 000
        self.client.post(self.url("estimate-action", estimate.pk), {"action": "submit", "comment": "Проверьте"})
        self.client.force_login(self.customer)
        page = self.client.get(self.url("estimate"))
        self.assertContains(page, "Утвердить смету")
        self.assertContains(page, "Проверьте")
        self.client.post(self.url("estimate-action", estimate.pk), {"action": "approve"})
        estimate.refresh_from_db()
        self.assertEqual(estimate.status, "approved")
        self.client.post(self.url("start-works"))
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, "in_progress")
        approvals = self.client.get(self.url("approvals"))
        self.assertContains(approvals, "Смета, версия 1")
        search_page = self.client.get(self.url("search") + "?q=кабель")
        self.assertContains(search_page, "<mark>Кабел</mark>ь", html=False)

    def test_calculation_pages(self):
        self.client.force_login(self.customer)
        response = self.client.post(self.url("calculations"), {"name": "Стандарт", "note": ""})
        calculation = self.project.calculations.get()
        self.assertRedirects(response, self.url("calculation", calculation.pk))
        update = self.url("calculation-update", calculation.pk)
        self.client.post(update, {"action": "add", "work": "Штукатурка", "cost_type": "work", "unit": "м²", "volume": "30", "price": "450"})
        self.assertContains(self.client.get(self.url("calculation", calculation.pk)), "13 500,00 сом")
        response = self.client.post(update, {"action": "to_estimate", "stage": self.stage.pk})
        self.assertRedirects(response, self.url("estimate") + "#lines", fetch_redirect_response=False)
        self.assertEqual(Estimate.objects.get().total, Decimal("13500.00"))

    def test_rejected_version_stays_visible_and_dashboard_tells_who_acts(self):
        self.client.force_login(self.foreman)
        self.client.post(self.url("estimate-create"))
        estimate = Estimate.objects.get()
        self.client.post(self.url("estimate-line-add", estimate.pk), {"stage": self.stage.pk, "name": "Розетки", "cost_type": "work", "unit": "шт",
                                                                   "quantity": "10", "price": "500", "revision": estimate.revision})
        self.client.post(self.url("estimate-action", estimate.pk), {"action": "submit"})
        self.client.force_login(self.customer)
        self.assertContains(self.client.get(reverse("workspace:dashboard")), "ждёт вашего решения")
        self.client.post(self.url("estimate-action", estimate.pk), {"action": "reject", "reason": "Слишком дорого"})
        self.client.force_login(self.foreman)
        self.assertContains(self.client.get(reverse("workspace:dashboard")), "Заказчик отклонил смету")
        page = self.client.get(self.url("estimate"))
        self.assertContains(page, "Слишком дорого")
        self.assertContains(page, "Новая версия")

