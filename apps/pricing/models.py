"""Foreman's price list: his own works and prices (ТЗ FR14, UC-22)."""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower


class WorkCategory(models.TextChoices):
    DEMOLITION = "demolition", "Демонтаж"
    ELECTRIC = "electric", "Электрика"
    PLUMBING = "plumbing", "Сантехника"
    WALLS = "walls", "Стены и потолки"
    FLOORS = "floors", "Полы"
    TILES = "tiles", "Плитка"
    PAINTING = "painting", "Покраска и обои"
    DOORS = "doors", "Окна и двери"
    OTHER = "other", "Прочее"


class PriceItem(models.Model):
    """One work in a foreman's price list. Calculations copy the price, so editing it never changes saved data."""

    foreman = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="price_items")
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=16, choices=WorkCategory.choices, default=WorkCategory.OTHER)
    unit = models.CharField(max_length=20)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "name", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(price__gt=0), name="price_item_price_positive"),
            # «Система сохраняет актуальную цену для соответствующей работы и единицы измерения» — one price per work and unit.
            models.UniqueConstraint(Lower("name"), "unit", "foreman", name="unique_price_item_per_foreman"),
        ]

    def __str__(self):
        return f"{self.name}, {self.unit}"
