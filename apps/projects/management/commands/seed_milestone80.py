"""Adds the 60–80 % demo content (preliminary calculation, estimate with approval history) to an existing demo project.

Fresh databases get the same content from `seed_demo`; this command is for a demo project created before.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.estimates.services import (add_item, add_line, approve_estimate, create_calculation, create_draft, reject_estimate,
                                     submit_estimate, update_line)
from apps.projects.models import Project

DEMO_NAME = "Демонстрационный ремонт"
LINES = {
    "Демонтаж": [("Демонтаж плитки и обоев", "work", "м²", "120", "220"), ("Вывоз строительного мусора", "delivery", "рейс", "3", "4500")],
    "Электромонтаж": [("Прокладка кабеля", "work", "м", "260", "120"), ("Кабель ВВГнг 3×2,5", "material", "м", "260", "85"),
                      ("Сборка электрощитка", "work", "шт", "1", "8000")],
    "Штукатурка стен": [("Штукатурка стен по маякам", "work", "м²", "180", "450"), ("Гипсовая штукатурка 30 кг", "material", "мешок", "60", "520")],
    "Чистовая отделка": [("Укладка ламината", "work", "м²", "48", "650"), ("Ламинат 33 класс", "material", "м²", "52", "1450")],
}
GENERIC = [("Работы по этапу", "work", "компл.", "1", "15000")]


class Command(BaseCommand):
    help = "Add a preliminary calculation and an approved estimate (with one rejected version) to the existing demo project."

    @transaction.atomic
    def handle(self, *args, **options):
        project = Project.objects.filter(name=DEMO_NAME, created_by__email="customer@example.com").first()
        if project is None:
            raise CommandError("Demo project not found. Run `manage.py seed_demo` first.")
        if project.estimates.exists() or project.calculations.exists():
            self.stdout.write("Demo project already has an estimate or calculations; no changes made.")
            return
        customer = project.created_by
        foreman_member = project.members.filter(role="foreman", is_active=True).first()
        if foreman_member is None:
            raise CommandError("Demo project needs an active foreman.")
        foreman = foreman_member.user
        calculation = create_calculation(project=project, actor=customer, name="Стандартный ремонт",
                                         note="Ориентировочно, по средним ценам Бишкека.")
        area = str(project.area or 64)
        for work, unit, volume, price in [("Демонтаж покрытий", "м²", area, "250"), ("Электромонтаж под ключ", "м²", area, "900"),
                                          ("Штукатурка стен по маякам", "м²", "180", "450"), ("Чистовая отделка", "м²", area, "2200")]:
            add_item(calculation=calculation, actor=customer, work=work, unit=unit, volume=volume, price=price)

        def fill(estimate, price_factor="1"):
            for stage in project.stages.exclude(status="cancelled"):
                for name, cost_type, unit, quantity, price in LINES.get(stage.name, GENERIC):
                    estimate.refresh_from_db()
                    add_line(estimate=estimate, actor=foreman, revision=estimate.revision, stage=stage, name=name,
                             cost_type=cost_type, unit=unit, quantity=quantity, price=str(float(price) * float(price_factor)))
            estimate.refresh_from_db()
            return estimate

        first = fill(create_draft(project=project, actor=foreman), price_factor="1.15")
        submit_estimate(estimate=first, actor=foreman, comment="Первый вариант, материалы премиум-класса.")
        reject_estimate(estimate=first, actor=customer, reason="Дорого. Возьмите материалы среднего класса.")
        second = create_draft(project=project, actor=foreman)
        for line in second.lines.all():
            second.refresh_from_db()
            update_line(line=line, actor=foreman, revision=second.revision, price=str(round(float(line.price) / 1.15, 2)))
        second.refresh_from_db()
        submit_estimate(estimate=second, actor=foreman, comment="Материалы среднего класса, как просили.")
        approve_estimate(estimate=second, actor=customer)
        self.stdout.write(self.style.SUCCESS(f"Demo project {project.pk}: calculation and estimate v2 (approved) added."))
