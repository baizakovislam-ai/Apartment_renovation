"""Adds the 80 % demo content of the foreman's price list (ТЗ FR14) for the demo foreman.

The foreman gets a price list of typical works in Bishkek; the customer sees it in the demo project and can take works
from it into the preliminary calculation. Fresh databases get the same content from `seed_demo`.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.pricing.services import add_price_item

DEMO_FOREMAN = "foreman@example.com"
PRICES = [
    ("Демонтаж старой плитки", "demolition", "м²", "250"),
    ("Вывоз строительного мусора", "demolition", "компл.", "3500"),
    ("Монтаж электроточки (розетка, выключатель)", "electric", "точка", "450"),
    ("Прокладка кабеля в штробе", "electric", "м", "120"),
    ("Установка смесителя", "plumbing", "шт", "900"),
    ("Монтаж унитаза", "plumbing", "шт", "2500"),
    ("Штукатурка стен по маякам", "walls", "м²", "450"),
    ("Шпаклёвка стен под покраску", "walls", "м²", "300"),
    ("Стяжка пола", "floors", "м²", "400"),
    ("Укладка ламината", "floors", "м²", "350"),
    ("Укладка плитки на пол", "tiles", "м²", "900"),
    ("Облицовка стен плиткой", "tiles", "м²", "1000"),
    ("Покраска стен в 2 слоя", "painting", "м²", "200"),
    ("Поклейка обоев", "painting", "м²", "180"),
    ("Установка межкомнатной двери", "doors", "шт", "3000"),
]


def fill_price_list(foreman):
    """Give the foreman a price list once. Returns the number of added positions."""
    if foreman.price_items.exists():
        return 0
    for name, category, unit, price in PRICES:
        add_price_item(actor=foreman, name=name, category=category, unit=unit, price=price)
    return len(PRICES)


class Command(BaseCommand):
    help = "Fill the demo foreman's price list (80% milestone, FR14)."

    @transaction.atomic
    def handle(self, *args, **options):
        foreman = get_user_model().objects.filter(email=DEMO_FOREMAN).first()
        if foreman is None:
            raise CommandError("Demo foreman not found. Run `manage.py seed_demo` first.")
        added = fill_price_list(foreman)
        if not added:
            self.stdout.write("Demo foreman already has a price list; no changes made.")
            return
        self.stdout.write(self.style.SUCCESS(f"Demo foreman's price list: {added} works added."))
