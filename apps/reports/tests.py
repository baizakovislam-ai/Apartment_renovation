"""Abdulazis's 100 % part: the final project report and its PDF (ТЗ FR21)."""
import io
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from apps.estimates import change_services
from apps.estimates import services as estimate_services
from apps.projects.models import Project, ProjectMember
from apps.projects.services import create_project
from apps.repairs.models import Stage
from apps.repairs.services import create_stage
from .pdf import render_pdf
from .services import build_report


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class FinalReportTests(TestCase):
    def setUp(self):
        User = get_user_model()
        make = lambda email: User.objects.create_user(email=email, full_name=email.split("@")[0].title(), is_email_verified=True)
        self.customer, self.foreman, self.worker, self.stranger = map(make, ["customer@example.com", "foreman@example.com",
                                                                             "worker@example.com", "stranger@example.com"])
        self.project = create_project(user=self.customer, name="Квартира на Киевской", address="Бишкек, Киевская 100",
                                      budget=Decimal("30000"), area=Decimal("42.5"))
        self.foreman_member = ProjectMember.objects.create(project=self.project, user=self.foreman, role="foreman")
        ProjectMember.objects.create(project=self.project, user=self.worker, role="executor")
        self.today = timezone.localdate()
        self.stage = create_stage(project=self.project, actor=self.foreman, name="Плитка", start_date=self.today,
                                  end_date=self.today + timedelta(days=10), responsible=self.foreman_member)

    def approve_estimate(self):
        estimate = estimate_services.create_draft(project=self.project, actor=self.foreman)
        for name, cost_type, unit, quantity, price in [("Укладка плитки", "work", "м²", "20", "900"), ("Плиточный клей", "material", "мешок", "10", "500")]:
            estimate.refresh_from_db()
            estimate_services.add_line(estimate=estimate, actor=self.foreman, revision=estimate.revision, stage=self.stage, name=name,
                                       cost_type=cost_type, unit=unit, quantity=quantity, price=price)
        estimate.refresh_from_db()
        estimate_services.submit_estimate(estimate=estimate, actor=self.foreman)
        return estimate_services.approve_estimate(estimate=estimate, actor=self.customer)

    def test_report_without_estimate_and_stages_in_the_past(self):
        Stage.objects.filter(pk=self.stage.pk).update(start_date=self.today - timedelta(days=20), end_date=self.today - timedelta(days=5))
        report = build_report(self.project, self.customer)
        self.assertIsNone(report["final_cost"])
        self.assertEqual(report["expenses"], [])
        self.assertEqual((report["stages"][0]["days_late"], report["schedule"]["late_stages"]), (5, 1))
        self.assertEqual((report["customer"], report["foreman"], report["executors"]), (self.customer, self.foreman, [self.worker]))
        self.assertTrue(render_pdf(report).startswith(b"%PDF"))

    def test_final_cost_from_approved_estimate_with_approved_changes(self):
        base = self.approve_estimate()  # 18 000 + 5 000 = 23 000
        change = change_services.create_request(project=self.project, actor=self.foreman, reason="Подорожал клей")
        change_services.set_item(request=change, actor=self.foreman, revision=change.revision, action="update",
                                 line=base.lines.get(name="Плиточный клей"), price="600")
        change.refresh_from_db()
        change = change_services.submit_request(request=change, actor=self.foreman, revision=change.revision)
        change_services.approve_request(request=change, actor=self.customer)
        report = build_report(self.project, self.foreman)
        self.assertEqual(report["estimate"]["initial_total"], Decimal("23000.00"))
        self.assertEqual(report["estimate"]["changes_total"], Decimal("1000.00"))
        self.assertEqual(report["final_cost"], Decimal("24000.00"))
        self.assertEqual(report["budget_left"], Decimal("6000.00"))
        self.assertIn("утверждённой смете", report["cost_source"])
        pdf = render_pdf(report)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 5000)

    def test_only_customer_and_foreman_build_the_report(self):
        for user in [self.worker, self.stranger]:
            with self.subTest(user=user.email), self.assertRaises(PermissionDenied):
                build_report(self.project, user)

    def test_pages_and_api(self):
        self.approve_estimate()
        for user in [self.customer, self.foreman]:
            self.client.force_login(user)
            page = self.client.get(reverse("workspace:report", args=[self.project.pk]))
            self.assertContains(page, "Итоговый отчёт")
            self.assertContains(page, "23 000,00 сом")
            pdf = self.client.get(reverse("workspace:report-pdf", args=[self.project.pk]))
            self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"))
            self.assertIn("attachment", pdf["Content-Disposition"])
            self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse("workspace:report", args=[self.project.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("workspace:report-pdf", args=[self.project.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("workspace:project", args=[self.project.pk])), "Итоговый отчёт")
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse("workspace:report-pdf", args=[self.project.pk])).status_code, 404)

        api = APIClient()
        api.force_authenticate(self.customer)
        data = api.get(f"/api/projects/{self.project.pk}/report/").data
        self.assertEqual((data["final_cost"], data["estimate"]["number"], data["budget_left"]), (Decimal("23000.00"), 1, Decimal("7000.00")))
        response = api.get(f"/api/projects/{self.project.pk}/report/pdf/")
        self.assertEqual(response["Content-Type"], "application/pdf")
        api.force_authenticate(self.worker)
        self.assertEqual(api.get(f"/api/projects/{self.project.pk}/report/").status_code, 403)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DemoReportTests(TestCase):
    def test_demo_project_report_has_every_section(self):
        call_command("seed_demo", password="Demo-password-2026", stdout=io.StringIO())
        project = Project.objects.get(name="Демонстрационный ремонт")
        report = build_report(project, project.created_by)
        self.assertTrue(report["stages"] and report["done_tasks"] and report["remarks"])
        self.assertIsNotNone(report["final_cost"])
        self.assertTrue(render_pdf(report).startswith(b"%PDF"))
