"""Pages of the foreman's price list (ТЗ FR14, UC-22): his own list and the read-only view inside a project."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from apps.estimates.models import UNIT_SUGGESTIONS
from apps.pricing import services as price_services
from apps.pricing.models import WorkCategory
from .finance_views import finance_project
from .forms import PriceItemForm
from .views import context, form_service_error, run_service


def list_context(items, params):
    """Filtered items plus what the search bar and category tabs need."""
    all_items = list(items)
    filtered = list(price_services.filter_items(items, params))
    filters = {"q": (params.get("q") or "").strip(), "category": params.get("category") or ""}
    return {"items": filtered, "total_count": len(all_items), "category_tabs": price_services.category_counts(all_items),
            "filters": filters, "has_filters": any(filters.values()), "categories": WorkCategory.choices}


@login_required
def price_list(request):
    if not price_services.is_foreman(request.user):
        return render(request, "workspace/price_list.html", context(request, None, "price-list", not_foreman=price_services.NOT_FOREMAN))
    form = PriceItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            item = price_services.add_price_item(actor=request.user, **form.cleaned_data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, f"Добавлено в прайс-лист: {item.name} — за {item.unit}.")
            return redirect("workspace:price-list")
    return render(request, "workspace/price_list.html", context(
        request, None, "price-list", form=form, own=True, unit_options=UNIT_SUGGESTIONS,
        **list_context(request.user.price_items.all(), request.GET)))


@login_required
def price_item_edit(request, item_id):
    item = get_object_or_404(request.user.price_items, pk=item_id)
    form = PriceItemForm(request.POST or None, item=item)
    if request.method == "POST" and form.is_valid():
        try:
            price_services.update_price_item(item=item, actor=request.user, **form.cleaned_data)
        except APIException as error:
            form_service_error(form, error)
        else:
            messages.success(request, "Позиция прайс-листа сохранена. Уже сделанные расчёты и сметы не изменились.")
            return redirect("workspace:price-list")
    return render(request, "workspace/form.html", context(
        request, None, "price-list", form=form, title="Позиция прайс-листа", section="работы", eyebrow="Мой прайс-лист",
        subtitle=f"«{item.name}». Новая цена будет подставляться в новые расчёты.", cancel_href=reverse("workspace:price-list"),
        unit_options=UNIT_SUGGESTIONS))


@login_required
@require_POST
def price_item_delete(request, item_id):
    item = get_object_or_404(request.user.price_items, pk=item_id)
    name = item.name
    run_service(request, lambda: price_services.delete_price_item(item=item, actor=request.user), f"«{name}» удалена из прайс-листа.")
    return redirect("workspace:price-list")


@login_required
def project_price_list(request, project_id):
    """The customer sees the prices of his foreman; the foreman sees his own list here too, with a link to edit it."""
    project = finance_project(request, project_id)
    foreman = price_services.project_foreman(project)
    return render(request, "workspace/price_list.html", context(
        request, project, "project-price-list", foreman=foreman, own=False, is_own_list=foreman == request.user,
        **list_context(price_services.project_price_list(project), request.GET)))
