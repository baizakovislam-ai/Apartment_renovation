from django.contrib import admin
from .models import Stage


@admin.register(Stage)
class StageAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "project", "status", "start_date", "end_date"]
    list_filter = ["status"]
    search_fields = ["name", "project__name"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
