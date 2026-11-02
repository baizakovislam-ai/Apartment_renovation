"""Bekzat's 60 % part: expenses (ТЗ FR12) and financial information (ТЗ FR13).

Covers the statuses Черновик → На подтверждении → Подтверждён / Отклонён, field checks, roles, repeated clicks,
concurrent changes, supporting documents, the budget/estimate/remaining calculation and the links to the report,
approvals journal, stage and project deletion.
"""
import io
import shutil
import tempfile
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.estimates import services as estimate_services
from apps.estimates.search import approval_items, project_search
from apps.estimates.testing import mark_started
from apps.projects.models import Project, ProjectEvent, ProjectMember
from apps.projects.services import archive_project, create_project, delete_project
from apps.reports.pdf import render_pdf
from apps.reports.services import build_report
from apps.repairs.models import Stage
from apps.repairs.services import create_stage, delete_stage
from . import services
from .models import Expense

TEMP_MEDIA = tempfile.mkdtemp()


def jpeg(name="check.jpg", exif=False):
    buffer = io.BytesIO()
    image = Image.new("RGB", (40, 30), (200, 200, 200))
    if exif:
        data = image.getexif()
        data[0x010F] = "SecretCamera"  # Make
        image.save(buffer, format="JPEG", exif=data)
    else:
        image.save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


def pdf(name="invoice.pdf"):
    body = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
    return SimpleUploadedFile(name, body, content_type="application/pdf")


class FinanceTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        User = get_user_model()
        make = lambda email: User.objects.create_user(email=email, full_name=email.split("@")[0].title(), is_email_verified=True)
        self.customer, self.foreman, self.worker, self.stranger = map(make, ["customer@example.com", "foreman@example.com",
                                                                             "worker@example.com", "stranger@example.com"])
        self.project = create_project(user=self.customer, name="Квартира", budget=Decimal("30000"))
        self.foreman_member = ProjectMember.objects.create(project=self.project, user=self.foreman, role="foreman")
        self.worker_member = ProjectMember.objects.create(project=self.project, user=self.worker, role="executor")
        self.today = timezone.localdate()
        self.stage = create_stage(project=self.project, actor=self.foreman, name="Плитка", start_date=self.today,
                                  end_date=self.today + timedelta(days=10), responsible=self.foreman_member)

    def add(self, actor=None, submit=True, **extra):
        data = {"amount": "1500", "date": self.today, "recipient": "Строймаркет", "purpose": "Плиточный клей", "category": "material", **extra}
        return services.create_expense(project=self.project, actor=actor or self.foreman, submit=submit, **data)

    def approve_estimate(self):
        """Approved estimate for 23 000: works 18 000, materials 5 000."""
        estimate = estimate_services.create_draft(project=self.project, actor=self.foreman)
        for name, cost_type, unit, quantity, price in [("Укладка плитки", "work", "м²", "20", "900"), ("Клей", "material", "мешок", "10", "500")]:
            estimate.refresh_from_db()
            estimate_services.add_line(estimate=estimate, actor=self.foreman, revision=estimate.revision, stage=self.stage, name=name,
                                       cost_type=cost_type, unit=unit, quantity=quantity, price=price)
        estimate.refresh_from_db()
        estimate_services.submit_estimate(estimate=estimate, actor=self.foreman)
        return estimate_services.approve_estimate(estimate=estimate, actor=self.customer)


@override_settings(PRIVATE_MEDIA_ROOT=TEMP_MEDIA, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ExpenseWorkflowTests(FinanceTestCase):
    def test_foreman_draft_submit_and_customer_confirms(self):
        expense = self.add(submit=False)
        self.assertEqual((expense.status, expense.number), ("draft", 1))
        # Somebody else's draft is not ready yet: the customer does not see it.
        self.assertFalse(services.visible_expenses(self.project, self.customer).filter(pk=expense.pk).exists())
        self.assertTrue(services.visible_expenses(self.project, self.foreman).filter(pk=expense.pk).exists())
        expense = services.submit_expense(expense=expense, actor=self.foreman, revision=expense.revision)
        self.assertEqual((expense.status, expense.submitted_by), ("pending", self.foreman))
        self.assertTrue(services.visible_expenses(self.project, self.customer).filter(pk=expense.pk).exists())
        expense = services.confirm_expense(expense=expense, actor=self.customer, revision=expense.revision)
        self.assertEqual((expense.status, expense.decided_by), ("confirmed", self.customer))
        self.assertIsNotNone(expense.decided_at)
        actions = list(ProjectEvent.objects.filter(project=self.project, action__startswith="expense.").values_list("action", flat=True))
        self.assertEqual(sorted(actions), ["expense.confirmed", "expense.created", "expense.submitted"])

    def test_customer_adds_and_confirms_own_expense(self):
        expense = self.add(actor=self.customer)
        self.assertEqual(expense.status, "pending")
        self.assertEqual(services.confirm_expense(expense=expense, actor=self.customer).status, "confirmed")

    def test_reject_needs_reason_and_keeps_the_expense(self):
        expense = self.add()
        with self.assertRaises(ValidationError):
            services.reject_expense(expense=expense, actor=self.customer, reason="  ")
        expense = services.reject_expense(expense=expense, actor=self.customer, reason="Нет чека")
        self.assertEqual((expense.status, expense.decision_reason, expense.decided_by), ("rejected", "Нет чека", self.customer))
        with self.assertRaises(ValidationError):
            services.delete_expense(expense=expense, actor=self.foreman)  # rejected data stays (ТЗ 4.12)
        self.assertTrue(Expense.objects.filter(pk=expense.pk).exists())

    def test_only_customer_decides_and_executor_has_no_access(self):
        expense = self.add()
        with self.assertRaises(PermissionDenied):
            services.confirm_expense(expense=expense, actor=self.foreman)
        with self.assertRaises(PermissionDenied):
            services.reject_expense(expense=expense, actor=self.foreman, reason="нет")
        with self.assertRaises(PermissionDenied):
            self.add(actor=self.worker)
        with self.assertRaises(PermissionDenied):
            services.visible_expenses(self.project, self.worker)
        with self.assertRaises(PermissionDenied):
            self.add(actor=self.stranger)

    def test_decision_is_made_once(self):
        expense = self.add()
        services.confirm_expense(expense=expense, actor=self.customer)
        with self.assertRaisesMessage(ValidationError, "Решение уже принято"):
            services.confirm_expense(expense=expense, actor=self.customer)
        with self.assertRaises(ValidationError):
            services.reject_expense(expense=expense, actor=self.customer, reason="передумал")
        draft = self.add(submit=False)
        with self.assertRaisesMessage(ValidationError, "ещё не отправлен"):
            services.confirm_expense(expense=draft, actor=self.customer)

    def test_repeated_click_creates_one_expense(self):
        request_id = uuid.uuid4()
        first = self.add(request_id=request_id)
        second = self.add(request_id=request_id)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.project.expenses.count(), 1)
        with self.assertRaises(ValidationError):
            self.add(actor=self.customer, request_id=request_id)  # somebody else's request id

    def test_stale_revision_is_refused(self):
        expense = self.add(submit=False)
        old = expense.revision
        services.update_expense(expense=expense, actor=self.foreman, revision=old, amount="1600")
        with self.assertRaisesMessage(ValidationError, "уже изменил другой пользователь"):
            services.update_expense(expense=expense, actor=self.foreman, revision=old, amount="1700")
        expense.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "уже изменил другой пользователь"):
            services.submit_expense(expense=expense, actor=self.foreman, revision=old)

    def test_only_author_edits_and_deletes_own_draft(self):
        expense = self.add(submit=False)
        with self.assertRaisesMessage(ValidationError, "только автор"):
            services.update_expense(expense=expense, actor=self.customer, amount="10")
        with self.assertRaisesMessage(ValidationError, "только автор"):
            services.delete_expense(expense=expense, actor=self.customer)
        expense = services.update_expense(expense=expense, actor=self.foreman, amount="2000,50", purpose="Затирка")
        self.assertEqual((expense.amount, expense.purpose), (Decimal("2000.50"), "Затирка"))
        services.submit_expense(expense=expense, actor=self.foreman)
        with self.assertRaisesMessage(ValidationError, "только черновик"):
            services.update_expense(expense=expense, actor=self.foreman, amount="1")
        with self.assertRaisesMessage(ValidationError, "только автор"):
            services.withdraw_expense(expense=expense, actor=self.customer)
        expense = services.withdraw_expense(expense=expense, actor=self.foreman)
        self.assertEqual((expense.status, expense.submitted_at), ("draft", None))
        services.delete_expense(expense=expense, actor=self.foreman)
        self.assertFalse(Expense.objects.filter(pk=expense.pk).exists())
        self.assertTrue(ProjectEvent.objects.filter(project=self.project, action="expense.deleted").exists())

    def test_confirmed_expense_cannot_change(self):
        expense = services.confirm_expense(expense=self.add(), actor=self.customer)
        for call in (lambda: services.update_expense(expense=expense, actor=self.foreman, amount="1"),
                     lambda: services.delete_expense(expense=expense, actor=self.foreman),
                     lambda: services.withdraw_expense(expense=expense, actor=self.foreman)):
            with self.assertRaises(ValidationError):
                call()

    def test_archived_project_is_read_only(self):
        expense = self.add()
        archive_project(project=self.project, actor=self.customer, reason="Пауза")
        with self.assertRaisesMessage(ValidationError, "в архиве"):
            self.add()
        with self.assertRaisesMessage(ValidationError, "в архиве"):
            services.confirm_expense(expense=expense, actor=self.customer)


@override_settings(PRIVATE_MEDIA_ROOT=TEMP_MEDIA, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ExpenseFieldChecksTests(FinanceTestCase):
    def assert_field_error(self, field, **data):
        with self.assertRaises(ValidationError) as caught:
            self.add(**data)
        self.assertIn(field, caught.exception.detail, caught.exception.detail)

    def test_amount(self):
        for value in ["0", "-5", "abc", "", "10.555", "1000000000000"]:
            with self.subTest(amount=value):
                self.assert_field_error("amount", amount=value)
        self.assertEqual(self.add(amount="1 250,5").amount, Decimal("1250.50"))

    def test_date_recipient_purpose_category(self):
        self.assert_field_error("date", date=self.today + timedelta(days=1))
        self.assert_field_error("date", date=None)
        self.assert_field_error("recipient", recipient="   ")
        self.assert_field_error("purpose", purpose="")
        self.assert_field_error("category", category="food")

    def test_stage_must_be_active_and_of_this_project(self):
        other = create_project(user=self.customer, name="Дача")
        other_stage = create_stage(project=other, actor=self.customer, name="Крыша", start_date=self.today, end_date=self.today,
                                   responsible=other.members.get())
        self.assert_field_error("stage", stage=other_stage)
        Stage.objects.filter(pk=self.stage.pk).update(status="cancelled")
        self.stage.refresh_from_db()
        self.assert_field_error("stage", stage=self.stage)

    def test_unknown_field(self):
        with self.assertRaisesMessage(ValidationError, "Неизвестные поля"):
            self.add(status="confirmed")

    def test_documents_are_checked_by_content(self):
        expense = self.add(document=pdf())
        self.assertEqual((expense.document_type, expense.document_name), ("application/pdf", "invoice.pdf"))
        self.assertTrue(Path(expense.document.path).exists())
        photo = self.add(document=jpeg(exif=True))
        self.assertEqual(photo.document_type, "image/jpeg")
        with Image.open(photo.document.path) as stored:
            self.assertNotIn(0x010F, stored.getexif())  # metadata removed
        for bad in [SimpleUploadedFile("fake.pdf", b"MZ\x90\x00 not a pdf", content_type="application/pdf"),
                    SimpleUploadedFile("cut.pdf", b"%PDF-1.4 broken without end", content_type="application/pdf"),
                    SimpleUploadedFile("photo.jpg", b"just text", content_type="image/jpeg")]:
            with self.subTest(name=bad.name):
                self.assert_field_error("document", document=bad)

    @override_settings(PHOTO_MAX_BYTES=100)
    def test_document_size_limit(self):
        self.assert_field_error("document", document=jpeg())

    def test_replacing_and_removing_document(self):
        expense = self.add(submit=False, document=pdf())
        first = Path(expense.document.path)
        with self.captureOnCommitCallbacks(execute=True):
            expense = services.update_expense(expense=expense, actor=self.foreman, document=jpeg())
        self.assertEqual(expense.document_type, "image/jpeg")
        self.assertFalse(first.exists())
        second = Path(expense.document.path)
        with self.captureOnCommitCallbacks(execute=True):
            expense = services.update_expense(expense=expense, actor=self.foreman, remove_document=True)
        self.assertFalse(expense.document)
        self.assertFalse(second.exists())


@override_settings(PRIVATE_MEDIA_ROOT=TEMP_MEDIA, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class FinanceSummaryTests(FinanceTestCase):
    def test_budget_estimate_actual_remaining_and_deviation(self):
        self.approve_estimate()
        services.confirm_expense(expense=self.add(amount="6000", category="material", stage=self.stage), actor=self.customer)
        services.confirm_expense(expense=self.add(amount="10000", category="work"), actor=self.customer)
        self.add(amount="2000")  # pending — not an actual cost yet
        services.reject_expense(expense=self.add(amount="500"), actor=self.customer, reason="Дубль")
        self.add(amount="900", submit=False)  # draft
        summary = services.finance_summary(self.project)
        self.assertEqual(summary["budget"], Decimal("30000"))
        self.assertEqual(summary["estimate_total"], Decimal("23000.00"))
        self.assertEqual(summary["spent"], Decimal("16000.00"))
        self.assertEqual((summary["pending_total"], summary["pending_count"], summary["rejected_count"]), (Decimal("2000.00"), 1, 1))
        self.assertEqual(summary["remaining"], Decimal("14000.00"))
        self.assertEqual(summary["deviation"], Decimal("-7000.00"))
        self.assertEqual(summary["deviation_percent"], -30.4)
        self.assertFalse(summary["over_budget"])
        by_category = {row["label"]: row for row in summary["by_category"]}
        self.assertEqual((by_category["Материалы"]["planned"], by_category["Материалы"]["actual"], by_category["Материалы"]["delta"]),
                         (Decimal("5000.00"), Decimal("6000.00"), Decimal("1000.00")))
        self.assertTrue(by_category["Материалы"]["over"])
        self.assertFalse(by_category["Работы"]["over"])
        by_stage = {row["label"]: row for row in summary["by_stage"]}
        self.assertEqual(by_stage["Плитка"]["actual"], Decimal("6000.00"))
        self.assertEqual(by_stage["Без привязки к этапу"]["actual"], Decimal("10000.00"))
        self.assertTrue(any("Материалы" in warning for warning in summary["warnings"]))

    def test_over_budget_warning(self):
        services.confirm_expense(expense=self.add(amount="29000"), actor=self.customer)
        self.add(amount="2000")
        self.assertIn("ожидающие", " ".join(services.finance_summary(self.project)["warnings"]))
        services.confirm_expense(expense=self.add(amount="1500"), actor=self.customer)
        summary = services.finance_summary(self.project)
        self.assertTrue(summary["over_budget"])
        self.assertEqual(summary["remaining"], Decimal("-500.00"))
        self.assertIn("превысили лимит", summary["warnings"][0])

    def test_without_budget_and_estimate(self):
        Project.objects.filter(pk=self.project.pk).update(budget=None)
        self.project.refresh_from_db()
        summary = services.finance_summary(self.project)
        self.assertEqual((summary["remaining"], summary["deviation"], summary["estimate_total"], summary["spent"]), (None, None, None, Decimal("0.00")))


@override_settings(PRIVATE_MEDIA_ROOT=TEMP_MEDIA, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ExpenseLinksTests(FinanceTestCase):
    def test_stage_with_expenses_cannot_be_deleted(self):
        self.add(stage=self.stage, submit=False)
        with self.assertRaisesMessage(ValidationError, "связанные записи"):
            delete_stage(stage=self.stage, actor=self.foreman)

    def test_project_deletion_removes_expenses_and_files(self):
        expense = self.add(document=pdf())
        path = Path(expense.document.path)
        self.assertIn("расходов: 1", services_summary(self.project))
        with self.captureOnCommitCallbacks(execute=True):
            delete_project(project=self.project, actor=self.customer)
        self.assertFalse(Expense.objects.exists())
        self.assertFalse(path.exists())

    def test_final_report_uses_confirmed_expenses(self):
        self.approve_estimate()
        services.confirm_expense(expense=self.add(amount="12000"), actor=self.customer)
        self.add(amount="3000")  # pending: not in the report
        report = build_report(self.project, self.customer)
        self.assertEqual(len(report["expenses"]), 1)
        self.assertEqual((report["final_cost"], report["cost_source"]), (Decimal("12000.00"), "подтверждённым расходам"))
        self.assertEqual(report["budget_left"], Decimal("18000.00"))
        self.assertTrue(render_pdf(report).startswith(b"%PDF"))

    def test_approvals_journal_and_search(self):
        expense = self.add(purpose="Кабель ВВГ")
        self.add(submit=False, purpose="Кабель в черновике")
        items = approval_items(self.project, self.customer, {"type": "expense"})
        self.assertEqual([item["object_id"] for item in items], [expense.pk])
        self.assertEqual(items[0]["status"], "pending")
        services.confirm_expense(expense=expense, actor=self.customer)
        self.assertEqual(approval_items(self.project, self.customer, {"type": "expense", "status": "approved"})[0]["object_id"], expense.pk)
        self.assertEqual([e.pk for e in project_search(self.project, self.customer, "кабель")["expenses"]], [expense.pk])
        self.assertEqual(len(project_search(self.project, self.foreman, "кабель")["expenses"]), 2)  # own draft too
        self.assertEqual(project_search(self.project, self.worker, "кабель")["expenses"], [])

    def test_filters(self):
        first = self.add(category="material", recipient="Леруа", stage=self.stage, date=self.today - timedelta(days=5))
        second = self.add(category="work", recipient="Бригада Асан", purpose="Укладка")
        services.confirm_expense(expense=second, actor=self.customer)
        visible = services.visible_expenses(self.project, self.customer)
        pks = lambda params: sorted(services.filter_expenses(visible, params).values_list("pk", flat=True))
        self.assertEqual(pks({"q": "асан"}), [second.pk])
        self.assertEqual(pks({"category": "material"}), [first.pk])
        self.assertEqual(pks({"status": "confirmed"}), [second.pk])
        self.assertEqual(pks({"stage": str(self.stage.pk)}), [first.pk])
        self.assertEqual(pks({"stage": "none"}), [second.pk])
        self.assertEqual(pks({"date_to": (self.today - timedelta(days=1)).isoformat()}), [first.pk])
        self.assertEqual(pks({"date_from": self.today.isoformat()}), [second.pk])
        self.assertEqual(pks({"category": "nonsense", "status": "bad", "date_from": "garbage"}), sorted([first.pk, second.pk]))


def services_summary(project):
    from apps.projects.services import project_delete_summary
    return " ".join(project_delete_summary(project))


@override_settings(PRIVATE_MEDIA_ROOT=TEMP_MEDIA, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ExpenseAPITests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.list_url = f"/api/projects/{self.project.pk}/expenses/"

    def test_create_with_document_confirm_and_summary(self):
        self.api.force_authenticate(self.foreman)
        response = self.api.post(self.list_url, {"amount": "4500.00", "date": self.today.isoformat(), "recipient": "Строймаркет",
                                                 "purpose": "Клей", "category": "material", "stage": self.stage.pk, "submit": "true",
                                                 "document": pdf()}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data["status"], response.data["amount"]), ("pending", "4500.00"))
        expense_id, document_url = response.data["id"], response.data["document_url"]
        self.assertEqual(self.api.post(f"/api/expenses/{expense_id}/action/", {"action": "confirm"}).status_code, 403)
        self.api.force_authenticate(self.customer)
        response = self.api.post(f"/api/expenses/{expense_id}/action/", {"action": "confirm", "revision": response.data["revision"]})
        self.assertEqual((response.status_code, response.data["status"]), (200, "confirmed"))
        summary = self.api.get(f"/api/projects/{self.project.pk}/finance/").data
        self.assertEqual((summary["spent"], summary["remaining"], summary["budget"]), ("4500.00", "25500.00", "30000.00"))
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(document_url).status_code, 200)
        self.assertEqual(self.api.get(self.list_url, {"status": "confirmed"}).data[0]["id"], expense_id)

    def test_validation_errors_come_back_as_400(self):
        self.api.force_authenticate(self.customer)
        response = self.api.post(self.list_url, {"amount": "-1", "date": self.today.isoformat(), "recipient": "X", "purpose": "Y"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("amount", response.data)
        future = (self.today + timedelta(days=3)).isoformat()
        response = self.api.post(self.list_url, {"amount": "10", "date": future, "recipient": "X", "purpose": "Y"}, format="json")
        self.assertEqual((response.status_code, "date" in response.data), (400, True))

    def test_executor_and_stranger_have_no_access(self):
        expense = self.add(document=pdf())
        self.api.force_authenticate(self.worker)
        self.assertEqual(self.api.get(self.list_url).status_code, 403)
        self.assertEqual(self.api.post(self.list_url, {"amount": "1", "date": self.today.isoformat(), "recipient": "X", "purpose": "Y"}).status_code, 403)
        self.assertEqual(self.api.get(f"/api/projects/{self.project.pk}/finance/").status_code, 403)
        self.assertEqual(self.api.get(f"/api/expenses/{expense.pk}/").status_code, 403)
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse("expense-document", args=[expense.pk])).status_code, 404)
        self.api.force_authenticate(self.stranger)
        self.assertEqual(self.api.get(self.list_url).status_code, 404)
        self.assertEqual(self.api.get(f"/api/expenses/{expense.pk}/").status_code, 404)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse("expense-document", args=[expense.pk])).status_code, 404)
        self.client.force_login(self.foreman)
        self.assertEqual(self.client.get(reverse("expense-document", args=[expense.pk]))["X-Content-Type-Options"], "nosniff")

    def test_patch_and_delete_own_draft_only(self):
        draft = self.add(submit=False)
        self.api.force_authenticate(self.customer)
        self.assertEqual(self.api.get(f"/api/expenses/{draft.pk}/").status_code, 404)  # not visible to others
        self.api.force_authenticate(self.foreman)
        response = self.api.patch(f"/api/expenses/{draft.pk}/", {"amount": "999.99", "revision": draft.revision}, format="json")
        self.assertEqual((response.status_code, response.data["amount"]), (200, "999.99"))
        stale = self.api.patch(f"/api/expenses/{draft.pk}/", {"amount": "1", "revision": draft.revision}, format="json")
        self.assertEqual(stale.status_code, 400)
        self.assertEqual(self.api.delete(f"/api/expenses/{draft.pk}/").status_code, 204)
        self.assertFalse(Expense.objects.filter(pk=draft.pk).exists())


@override_settings(PRIVATE_MEDIA_ROOT=TEMP_MEDIA, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ExpensePagesTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        mark_started(self.project)
        self.create_url = reverse("workspace:expense-create", args=[self.project.pk])

    def post_form(self, user, **extra):
        self.client.force_login(user)
        data = {"amount": "2500", "date": self.today.isoformat(), "recipient": "Строймаркет", "purpose": "Грунтовка",
                "category": "material", "send_now": "on", "request_id": str(uuid.uuid4()), **extra}
        return self.client.post(self.create_url, data)

    def test_pages_open_for_managers_and_are_closed_to_executor(self):
        expense = self.add()
        names = [("workspace:finances", []), ("workspace:expenses", []), ("workspace:expense-create", []), ("workspace:expense", [expense.pk])]
        for user in (self.customer, self.foreman):
            self.client.force_login(user)
            for name, args in names:
                with self.subTest(user=user.email, page=name):
                    self.assertEqual(self.client.get(reverse(name, args=[self.project.pk, *args])).status_code, 200)
        self.client.force_login(self.worker)
        for name, args in names:
            with self.subTest(user="executor", page=name):
                self.assertEqual(self.client.get(reverse(name, args=[self.project.pk, *args])).status_code, 403)
        page = self.client.get(reverse("workspace:project", args=[self.project.pk]))
        self.assertNotContains(page, reverse("workspace:expenses", args=[self.project.pk]))  # no menu item for the executor

    def test_create_through_form_and_double_click(self):
        request_id = str(uuid.uuid4())
        response = self.post_form(self.foreman, request_id=request_id, document=jpeg())
        expense = Expense.objects.get()
        self.assertRedirects(response, reverse("workspace:expense", args=[self.project.pk, expense.pk]))
        self.assertEqual((expense.status, expense.document_type), ("pending", "image/jpeg"))
        self.post_form(self.foreman, request_id=request_id)
        self.assertEqual(Expense.objects.count(), 1)
        page = self.client.get(reverse("workspace:expense", args=[self.project.pk, expense.pk]))
        self.assertContains(page, "Ждёт решения заказчика")
        self.assertContains(page, "Отозвать и исправить")

    def test_form_errors_are_shown(self):
        response = self.post_form(self.customer, amount="0", date=(self.today + timedelta(days=1)).isoformat(), recipient="")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Expense.objects.exists())
        self.assertContains(response, "Дата расхода не может быть в будущем")
        response = self.post_form(self.customer, document=SimpleUploadedFile("virus.pdf", b"MZ fake", content_type="application/pdf"))
        self.assertContains(response, "допустимы только PDF")
        self.assertFalse(Expense.objects.exists())

    def test_customer_confirms_and_rejects_on_the_page(self):
        first, second = self.add(amount="1000"), self.add(amount="700")
        self.client.force_login(self.customer)
        dashboard = self.client.get(reverse("workspace:dashboard") + f"?project={self.project.pk}")
        self.assertContains(dashboard, "Расходов ждут подтверждения: 2")
        detail = self.client.get(reverse("workspace:expense", args=[self.project.pk, first.pk]))
        self.assertContains(detail, "Подтвердить 1 000,00 сом")
        action = lambda expense, **data: self.client.post(reverse("workspace:expense-action", args=[self.project.pk, expense.pk]),
                                                          {"revision": expense.revision, **data})
        action(first, action="confirm")
        action(second, action="reject", reason="")
        second.refresh_from_db()
        self.assertEqual(second.status, "pending")  # a reason is required
        action(second, action="reject", reason="Сумма не совпадает с чеком")
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, second.status), ("confirmed", "rejected"))
        finances = self.client.get(reverse("workspace:finances", args=[self.project.pk]))
        self.assertContains(finances, "1 000,00 сом")
        self.assertContains(finances, "29 000,00 сом")  # remaining budget
        self.client.force_login(self.foreman)
        dashboard = self.client.get(reverse("workspace:dashboard") + f"?project={self.project.pk}")
        self.assertContains(dashboard, f"Заказчик отклонил расход №{second.number}")
        response = self.client.post(reverse("workspace:expense-action", args=[self.project.pk, first.pk]), {"action": "confirm"})
        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        self.assertEqual(first.decided_by, self.customer)  # the foreman cannot decide

    def test_list_filters_and_edit_page(self):
        draft = self.add(submit=False, purpose="Саморезы")
        self.add(purpose="Шпаклёвка", category="material")
        self.client.force_login(self.foreman)
        page = self.client.get(reverse("workspace:expenses", args=[self.project.pk]), {"q": "саморез"})
        self.assertContains(page, "Саморезы")
        self.assertNotContains(page, "Шпаклёвка")
        edit_url = reverse("workspace:expense-edit", args=[self.project.pk, draft.pk])
        response = self.client.post(edit_url, {"amount": "300", "date": self.today.isoformat(), "recipient": "Строймаркет",
                                               "purpose": "Саморезы 4×50", "category": "material", "revision": draft.revision})
        self.assertRedirects(response, reverse("workspace:expense", args=[self.project.pk, draft.pk]))
        draft.refresh_from_db()
        self.assertEqual((draft.amount, draft.purpose), (Decimal("300.00"), "Саморезы 4×50"))
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(edit_url).status_code, 404)  # somebody else's draft is invisible
        self.client.force_login(self.foreman)
        self.client.post(reverse("workspace:expense-action", args=[self.project.pk, draft.pk]), {"action": "delete", "revision": draft.revision})
        self.assertFalse(Expense.objects.filter(pk=draft.pk).exists())


@override_settings(PRIVATE_MEDIA_ROOT=TEMP_MEDIA, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ExpenseDemoTests(TestCase):
    def test_demo_has_expenses_in_every_status(self):
        from django.core.management import call_command
        call_command("seed_demo", password="Demo-password-2026", stdout=io.StringIO())
        project = Project.objects.get(name="Демонстрационный ремонт")
        self.assertEqual(set(project.expenses.values_list("status", flat=True)), {"draft", "pending", "confirmed", "rejected"})
        summary = services.finance_summary(project)
        self.assertEqual(summary["spent"], Decimal("100600.00"))
        self.assertEqual(summary["remaining"], project.budget - Decimal("100600.00"))
        out = io.StringIO()
        call_command("seed_expenses", stdout=out)  # second run changes nothing
        self.assertIn("no changes", out.getvalue())
        self.assertEqual(project.expenses.count(), 7)
