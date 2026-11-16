"""Foreman's price list (ТЗ FR14, UC-22) and its use in the preliminary calculation (FR16, UC-23).

Who does what:
  keep a price list      — a foreman (a user who is the foreman in at least one project);
  add / edit / delete    — only the owner, only his own positions;
  view                   — the owner, and the customer of a project where he is the foreman;
  take into calculation  — customer and foreman of that project; the price is copied into the calculation,
                           so a later change of the price list never changes saved calculations or estimates (UC-22).
The executor has no access to prices at all (ТЗ 4.1.1, прил. А).
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models.functions import Lower
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.estimates import services as estimate_services
from apps.estimates.models import CostType
from apps.projects.models import ProjectMember
from .models import PriceItem, WorkCategory

FIELDS = {"name", "category", "unit", "price"}
NOT_FOREMAN = "Прайс-лист ведёт прораб. Он станет доступен, когда вы будете прорабом хотя бы в одном проекте."


# ---------- reading ----------

def is_foreman(user):
    if user is None or not user.is_authenticated:
        return False
    return ProjectMember.objects.filter(user=user, role=ProjectMember.Role.FOREMAN, is_active=True).exists()


def require_foreman(user):
    if not is_foreman(user):
        raise PermissionDenied(NOT_FOREMAN)


def project_foreman(project):
    member = project.members.filter(role=ProjectMember.Role.FOREMAN, is_active=True).select_related("user").first()
    return member.user if member else None


def project_price_list(project):
    """Price list of the project's current foreman; empty while the project has no foreman."""
    foreman = project_foreman(project)
    return PriceItem.objects.filter(foreman=foreman) if foreman else PriceItem.objects.none()


def filter_items(items, params):
    """Search by name and filter by category for the price list page and the API."""
    query, category = (params.get("q") or "").strip(), params.get("category") or ""
    if query:
        items = items.filter(name__icontains=query)
    if category in WorkCategory.values:
        items = items.filter(category=category)
    return items


def category_counts(items):
    counts = {}
    for item in items:
        counts[item.category] = counts.get(item.category, 0) + 1
    return [(value, label, counts[value]) for value, label in WorkCategory.choices if value in counts]


def price_key(name, unit, category):
    return (" ".join(name.split()).casefold(), " ".join(unit.split()).casefold(), category)


def _average_prices(requested):
    if not requested:
        return {}

    categories = {key[2] for key in requested}
    active_foremen = ProjectMember.objects.filter(
        role=ProjectMember.Role.FOREMAN, is_active=True
    ).values_list("user_id", flat=True).distinct()
    prices = PriceItem.objects.filter(foreman_id__in=active_foremen, category__in=categories).only(
        "foreman_id", "name", "unit", "category", "price"
    )

    totals = {}
    for item in prices:
        key = price_key(item.name, item.unit, item.category)
        if key not in requested:
            continue
        total, foremen = totals.setdefault(key, (Decimal("0"), set()))
        if item.foreman_id not in foremen:
            totals[key] = (total + item.price, foremen | {item.foreman_id})

    result = {}
    for key, (total, foremen) in totals.items():
        average = (total / len(foremen)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        result[key] = {"average_price": average, "foremen_count": len(foremen)}
    return result


def average_prices_for(items):
    """Return an arithmetic mean per work/unit/category across active foremen's price lists."""
    return _average_prices({price_key(item.name, item.unit, item.category) for item in items})


def average_price_for(*, name, unit, category):
    """Calculate the mean for one selected work, using the same identity as price list rows."""
    key = price_key(name, unit, category)
    return _average_prices({key}).get(key, {"average_price": None, "foremen_count": 0})


# ---------- changing ----------

def clean(data, foreman, item=None):
    unknown = set(data) - FIELDS
    if unknown:
        raise ValidationError(f"Неизвестные поля: {', '.join(sorted(unknown))}.")
    cleaned = dict(data)
    for field, label in (("name", "название работы"), ("unit", "единицу измерения")):
        if field in cleaned or item is None:
            cleaned[field] = " ".join((cleaned.get(field) or "").split())
            if not cleaned[field]:
                raise ValidationError({field: f"Укажите {label}."})
    if "category" in cleaned or item is None:
        if cleaned.get("category") not in WorkCategory.values:
            raise ValidationError({"category": "Выберите категорию работы."})
    if "price" in cleaned or item is None:
        cleaned["price"] = estimate_services.to_decimal(cleaned.get("price"), "price", "цену")
        if cleaned["price"] <= 0:
            raise ValidationError({"price": "Цена должна быть больше нуля."})
        if cleaned["price"] >= 10 ** 12:
            raise ValidationError({"price": "Слишком большая цена."})
        cleaned["price"] = estimate_services.money(cleaned["price"])
    name = cleaned.get("name", item.name if item else "")
    unit = cleaned.get("unit", item.unit if item else "")
    twins = PriceItem.objects.annotate(lower_name=Lower("name")).filter(foreman=foreman, lower_name=name.lower(), unit=unit)
    if item is not None:
        twins = twins.exclude(pk=item.pk)
    if twins.exists():
        raise ValidationError({"name": f"«{name}» за {unit} уже есть в прайс-листе. Измените цену у этой позиции."})
    return cleaned


def own_item(item, actor):
    """Lock the position; only its owner may change it."""
    item = PriceItem.objects.select_for_update().get(pk=item.pk)
    if item.foreman_id != actor.pk:
        raise PermissionDenied("Менять можно только позиции своего прайс-листа.")
    return item


@transaction.atomic
def add_price_item(*, actor, **data):
    require_foreman(actor)
    return PriceItem.objects.create(foreman=actor, **clean(data, actor))


@transaction.atomic
def update_price_item(*, item, actor, **data):
    item = own_item(item, actor)
    for key, value in clean(data, actor, item).items():
        setattr(item, key, value)
    item.save()
    return item


@transaction.atomic
def delete_price_item(*, item, actor):
    """A price list is the foreman's working reference, not approved data: calculations keep their own copy."""
    own_item(item, actor).delete()


# ---------- use in the preliminary calculation (FR16) ----------

def add_to_calculation(*, calculation, actor, price_item, volume):
    """Add a work from the project foreman's price list to the calculation with the current price."""
    project = calculation.project
    estimate_services.require_finance_access(project, actor)
    foreman = project_foreman(project)
    if price_item is None or foreman is None or price_item.foreman_id != foreman.pk:
        raise ValidationError({"price_item": "Выберите работу из прайс-листа прораба этого проекта."})
    return estimate_services.add_item(calculation=calculation, actor=actor, work=price_item.name, cost_type=CostType.WORK,
                                      unit=price_item.unit, volume=volume, price=price_item.price)
