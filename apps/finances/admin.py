from django.contrib import admin

from .models import Expense


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ["number", "project", "date", "purpose", "recipient", "category", "amount", "status", "created_by"]
    list_filter = ["status", "category"]
    search_fields = ["purpose", "recipient", "note"]
    readonly_fields = ["request_id", "revision", "created_at", "updated_at", "submitted_at", "decided_at"]
