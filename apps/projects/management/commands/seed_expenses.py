"""Adds the 60 % demo content of expenses (ТЗ FR12, FR13) to the demo project.

Expenses in every status: confirmed ones (the actual costs), one waiting for the customer (seen on «Главная»),
one rejected with a reason and a foreman's draft. Fresh databases get the same content from `seed_demo`;
this command is for a demo project created before.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.finances import services
from apps.projects.models import Project

DEMO_NAME = "Демонстрационный ремонт"


def fill_expenses(project):
    """Create demo expenses once. Returns how many were created."""
    foreman = project.members.filter(role="foreman", is_active=True).select_related("user").first()
    customer = project.members.filter(role="customer", is_active=True).select_related("user").first()
    if foreman is None or customer is None or project.expenses.exists():
        return 0
    today = timezone.localdate()
    stage = lambda name: project.stages.filter(name=name).first()
    rows = [  # (author, stage, days ago, amount, recipient, purpose, category, outcome)
        (foreman, "Демонтаж", 18, "18500", "ИП Асанов (вывоз мусора)", "Вывоз строительного мусора, 2 рейса", "delivery", "confirm"),
        (foreman, "Электромонтаж", 9, "42300", "Строймаркет «Ак-Кеме»", "Кабель ВВГнг 3×2,5 — 150 м, гофра, подрозетники", "material", "confirm"),
        (customer, "Электромонтаж", 7, "12000", "Бригада Нурлана", "Аванс за электромонтаж", "work", "confirm"),
        (foreman, "Штукатурка стен", 4, "27800", "ОсОО «Кнауф Центр»", "Штукатурка Ротбанд 30 кг × 40 мешков", "material", "confirm"),
        (foreman, "Штукатурка стен", 2, "6400", "Строймаркет «Ак-Кеме»", "Маяки, грунтовка, правило 2 м", "material", "pending"),
        (foreman, "Электромонтаж", 3, "9800", "Электрика-Сервис", "Автоматы и УЗО для щитка", "equipment", "reject"),
        (foreman, None, 1, "1500", "Такси-грузовое", "Доставка инструмента на объект", "delivery", "draft"),
    ]
    for author, stage_name, days, amount, recipient, purpose, category, outcome in rows:
        expense = services.create_expense(project=project, actor=author.user, submit=outcome != "draft", amount=amount,
                                          date=today - timedelta(days=days), recipient=recipient, purpose=purpose, category=category,
                                          stage=stage(stage_name) if stage_name else None)
        if outcome == "confirm":
            services.confirm_expense(expense=expense, actor=customer.user)
        elif outcome == "reject":
            services.reject_expense(expense=expense, actor=customer.user,
                                    reason="В смете заложены автоматы другого производителя — приложите счёт с ценами из сметы.")
    return len(rows)


class Command(BaseCommand):
    help = "Add demo expenses in every status (60% milestone, FR12, FR13) to the existing demo project."

    @transaction.atomic
    def handle(self, *args, **options):
        project = Project.objects.filter(name=DEMO_NAME, created_by__email="customer@example.com").first()
        if project is None:
            raise CommandError("Demo project not found. Run `manage.py seed_demo` first.")
        if project.expenses.exists():
            self.stdout.write("Demo project already has expenses; no changes made.")
            return
        count = fill_expenses(project)
        self.stdout.write(self.style.SUCCESS(f"Demo project {project.pk}: {count} expenses added."))
