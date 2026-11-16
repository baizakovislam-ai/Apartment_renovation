from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.projects.models import ProjectMember
from apps.projects.services import create_project
from .models import PriceItem


class AveragePriceTests(TestCase):
    def setUp(self):
        user = get_user_model()
        self.customer = user.objects.create_user(email="customer@example.com", full_name="Customer", is_email_verified=True)
        self.foremen = [
            user.objects.create_user(email=f"foreman{i}@example.com", full_name=f"Foreman {i}", is_email_verified=True)
            for i in range(2)
        ]
        self.project = create_project(user=self.customer, name="Ремонт")
        ProjectMember.objects.create(project=self.project, user=self.foremen[0], role=ProjectMember.Role.FOREMAN)
        other_project = create_project(user=self.customer, name="Другой ремонт")
        ProjectMember.objects.create(project=other_project, user=self.foremen[1], role=ProjectMember.Role.FOREMAN)
        for foreman, price in zip(self.foremen, ["400", "600"]):
            PriceItem.objects.create(foreman=foreman, name="Штукатурка стен", category="walls", unit="м²", price=price)
        self.client = APIClient()
        self.client.force_authenticate(self.customer)

    def test_project_average_api_and_price_list_page(self):
        response = self.client.get(
            f"/api/projects/{self.project.pk}/price-list/average/",
            {"name": "Штукатурка стен", "category": "walls", "unit": "м²"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["average_price"], Decimal("500.00"))
        self.assertEqual(response.data["foremen_count"], 2)

        self.client.force_login(self.customer)
        page = self.client.get(reverse("workspace:project-price-list", args=[self.project.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["items"][0].average_price, Decimal("500.00"))

    def test_average_is_empty_without_matching_prices(self):
        response = self.client.get(
            f"/api/projects/{self.project.pk}/price-list/average/",
            {"name": "Шпаклёвка", "category": "walls", "unit": "м²"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["average_price"])
        self.assertEqual(response.data["foremen_count"], 0)
