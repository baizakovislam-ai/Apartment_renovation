"""Abdulazis's 80 % part: the foreman's price list (ТЗ FR14, UC-22) and its use in the preliminary calculation."""
import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.estimates import services as estimate_services
from apps.projects.models import ProjectMember
from apps.projects.services import create_project
from . import services
from .models import PriceItem


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PriceListTests(TestCase):
    def setUp(self):
        User = get_user_model()
        make = lambda email: User.objects.create_user(email=email, full_name=email.split("@")[0].title(), is_email_verified=True)
        self.customer, self.foreman, self.worker, self.other_foreman = map(make, ["customer@example.com", "foreman@example.com",
                                                                                  "worker@example.com", "other@example.com"])
        self.project = create_project(user=self.customer, name="Квартира", budget=Decimal("100000"), area=Decimal("40"))
        ProjectMember.objects.create(project=self.project, user=self.foreman, role="foreman")
        ProjectMember.objects.create(project=self.project, user=self.worker, role="executor")
        other_project = create_project(user=self.customer, name="Дача")
        ProjectMember.objects.create(project=other_project, user=self.other_foreman, role="foreman")
        self.laminate = self.add(name="Укладка ламината", category="floors", unit="м²", price="350")

    def add(self, actor=None, **data):
        return services.add_price_item(actor=actor or self.foreman, **data)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_foreman_adds_edits_and_deletes_own_positions(self):
        self.assertEqual((self.laminate.name, self.laminate.category, self.laminate.unit, self.laminate.price),
                         ("Укладка ламината", "floors", "м²", Decimal("350.00")))
        services.update_price_item(item=self.laminate, actor=self.foreman, price="380,5", name="  Укладка   ламината  ")
        self.laminate.refresh_from_db()
        self.assertEqual((self.laminate.name, self.laminate.price), ("Укладка ламината", Decimal("380.50")))
        services.delete_price_item(item=self.laminate, actor=self.foreman)
        self.assertFalse(PriceItem.objects.exists())

    def test_required_fields_positive_price_and_one_price_per_work_and_unit(self):
        for data in [dict(name="", category="floors", unit="м²", price="1"), dict(name="Стяжка", category="floors", unit=" ", price="1"),
                     dict(name="Стяжка", category="space", unit="м²", price="1"), dict(name="Стяжка", category="floors", unit="м²", price="0"),
                     dict(name="Стяжка", category="floors", unit="м²", price="-5"), dict(name="Стяжка", category="floors", unit="м²", price="abc"),
                     dict(name="укладка ЛАМИНАТА", category="floors", unit="м²", price="400")]:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                self.add(**data)
        with self.assertRaises(ValidationError):
            self.add(name="Стяжка", category="floors", unit="м²", price="400", colour="red")
        self.add(name="Укладка ламината", category="floors", unit="м.п.", price="120")  # another unit is another price
        other = self.add(name="Стяжка пола", category="floors", unit="м²", price="400")
        with self.assertRaises(ValidationError):
            services.update_price_item(item=other, actor=self.foreman, name="Укладка ламината")
        self.add(actor=self.other_foreman, name="Укладка ламината", category="floors", unit="м²", price="300")  # own list

    def test_only_foreman_keeps_a_price_list_and_only_owner_changes_it(self):
        for user in [self.customer, self.worker]:
            with self.subTest(user=user.email), self.assertRaises(PermissionDenied):
                self.add(actor=user, name="Стяжка", category="floors", unit="м²", price="400")
        for call in [lambda: services.update_price_item(item=self.laminate, actor=self.other_foreman, price="1"),
                     lambda: services.delete_price_item(item=self.laminate, actor=self.other_foreman)]:
            with self.assertRaises(PermissionDenied):
                call()
        removed = ProjectMember.objects.get(user=self.foreman)
        removed.is_active = False
        removed.save()
        self.assertFalse(services.is_foreman(self.foreman))
        self.assertEqual(list(services.project_price_list(self.project)), [])

    def test_price_into_calculation_is_copied_and_later_changes_do_not_touch_it(self):
        calculation = estimate_services.create_calculation(project=self.project, actor=self.customer, name="Эконом")
        item = services.add_to_calculation(calculation=calculation, actor=self.customer, price_item=self.laminate, volume="40")
        self.assertEqual((item.work, item.unit, item.price, item.amount, item.cost_type),
                         ("Укладка ламината", "м²", Decimal("350.00"), Decimal("14000.00"), "work"))
        services.update_price_item(item=self.laminate, actor=self.foreman, price="500")
        services.delete_price_item(item=self.laminate, actor=self.foreman)
        item.refresh_from_db()
        self.assertEqual(item.price, Decimal("350.00"))
        foreign = self.add(actor=self.other_foreman, name="Покраска", category="painting", unit="м²", price="200")
        for user, price_item, volume, error in [(self.customer, foreign, "1", ValidationError), (self.customer, None, "1", ValidationError),
                                                (self.worker, foreign, "1", PermissionDenied)]:
            with self.subTest(user=user.email), self.assertRaises(error):
                services.add_to_calculation(calculation=calculation, actor=user, price_item=price_item, volume=volume)
        mine = self.add(name="Стяжка пола", category="floors", unit="м²", price="400")
        with self.assertRaises(ValidationError):
            services.add_to_calculation(calculation=calculation, actor=self.customer, price_item=mine, volume="0")

    def test_search_and_category_filter(self):
        self.add(name="Стяжка пола", category="floors", unit="м²", price="400")
        self.add(name="Монтаж розетки", category="electric", unit="точка", price="450")
        items = self.foreman.price_items.all()
        self.assertEqual([i.name for i in services.filter_items(items, {"q": "стяжка"})], ["Стяжка пола"])
        self.assertEqual([i.name for i in services.filter_items(items, {"category": "electric"})], ["Монтаж розетки"])
        self.assertEqual(len(services.filter_items(items, {"category": "unknown"})), 3)
        self.assertEqual(services.category_counts(items), [("electric", "Электрика", 1), ("floors", "Полы", 2)])

    def test_api(self):
        client = self.api(self.foreman)
        created = client.post("/api/price-list/", {"name": "Стяжка пола", "category": "floors", "unit": "м²", "price": "400"}, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["category_name"], "Полы")
        pk = created.data["id"]
        self.assertEqual(client.patch(f"/api/price-list/{pk}/", {"price": "420"}, format="json").data["price"], "420.00")
        self.assertEqual(client.post("/api/price-list/", {"name": "Стяжка", "category": "floors", "unit": "м²", "price": "-1"},
                                     format="json").status_code, 400)
        self.assertEqual([row["name"] for row in client.get("/api/price-list/?q=стяж").data], ["Стяжка пола"])
        self.assertEqual(self.api(self.other_foreman).patch(f"/api/price-list/{pk}/", {"price": "1"}, format="json").status_code, 404)
        self.assertEqual(self.api(self.customer).get("/api/price-list/").status_code, 403)
        self.assertEqual(len(self.api(self.customer).get(f"/api/projects/{self.project.pk}/price-list/").data), 2)
        self.assertEqual(self.api(self.worker).get(f"/api/projects/{self.project.pk}/price-list/").status_code, 403)
        self.assertEqual(self.api(self.other_foreman).get(f"/api/projects/{self.project.pk}/price-list/").status_code, 404)
        calculation = estimate_services.create_calculation(project=self.project, actor=self.customer, name="Вариант")
        added = self.api(self.customer).post(f"/api/calculations/{calculation.pk}/from-price-list/",
                                             {"price_item": pk, "volume": "10"}, format="json")
        self.assertEqual((added.status_code, added.data["amount"]), (201, "4200.00"))
        self.assertEqual(client.delete(f"/api/price-list/{pk}/").status_code, 204)

    def test_pages(self):
        self.client.force_login(self.foreman)
        page = self.client.get(reverse("workspace:price-list"))
        self.assertContains(page, "Мой прайс-лист")
        self.assertContains(page, "Укладка ламината")
        response = self.client.post(reverse("workspace:price-list"), {"name": "Стяжка пола", "category": "floors", "unit": "м²", "price": "400"})
        self.assertRedirects(response, reverse("workspace:price-list"))
        duplicate = self.client.post(reverse("workspace:price-list"), {"name": "стяжка пола", "category": "floors", "unit": "м²", "price": "1"})
        self.assertContains(duplicate, "уже есть в прайс-листе")
        self.client.post(reverse("workspace:price-item-edit", args=[self.laminate.pk]),
                         {"name": "Укладка ламината", "category": "floors", "unit": "м²", "price": "390"})
        self.laminate.refresh_from_db()
        self.assertEqual(self.laminate.price, Decimal("390.00"))
        self.client.post(reverse("workspace:price-item-delete", args=[self.laminate.pk]))
        self.assertFalse(PriceItem.objects.filter(pk=self.laminate.pk).exists())

        self.client.force_login(self.other_foreman)
        self.assertEqual(self.client.get(reverse("workspace:price-item-edit", args=[self.foreman.price_items.get().pk])).status_code, 404)

        self.client.force_login(self.customer)
        self.assertNotContains(self.client.get(reverse("workspace:price-list")), "Добавить работу")
        project_page = self.client.get(reverse("workspace:project-price-list", args=[self.project.pk]))
        self.assertContains(project_page, "Стяжка пола")
        self.assertNotContains(project_page, "Добавить работу")
        calculation = estimate_services.create_calculation(project=self.project, actor=self.customer, name="Вариант")
        url = reverse("workspace:calculation", args=[self.project.pk, calculation.pk])
        self.assertContains(self.client.get(url), "Из прайс-листа прораба")
        self.client.post(reverse("workspace:calculation-update", args=[self.project.pk, calculation.pk]),
                         {"action": "from_price_list", "price_item": self.foreman.price_items.get().pk, "volume": "40"})
        self.assertEqual(calculation.items.get().amount, Decimal("16000.00"))

        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse("workspace:project-price-list", args=[self.project.pk])).status_code, 403)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PriceListSeedTests(TestCase):
    def test_seed_pricelist(self):
        call_command("seed_demo", password="Demo-password-2026", stdout=io.StringIO())
        foreman = get_user_model().objects.get(email="foreman@example.com")
        count = foreman.price_items.count()
        self.assertGreaterEqual(count, 10)
        call_command("seed_pricelist", stdout=io.StringIO())  # repeat run changes nothing
        self.assertEqual(foreman.price_items.count(), count)
