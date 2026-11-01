from django.contrib import admin

from .models import PriceItem


@admin.register(PriceItem)
class PriceItemAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "unit", "price", "foreman", "updated_at"]
    list_filter = ["category"]
    search_fields = ["name", "foreman__email"]
