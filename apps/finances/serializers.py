from django.urls import reverse
from rest_framework import serializers

from apps.repairs.models import Stage
from .models import Expense


class ExpenseSerializer(serializers.ModelSerializer):
    stage = serializers.PrimaryKeyRelatedField(queryset=Stage.objects.none(), required=False, allow_null=True)
    # Numbers are checked in the service, so the API and the pages give the same messages.
    amount = serializers.CharField()
    date = serializers.DateField()
    request_id = serializers.UUIDField(required=False, write_only=True)
    document_url = serializers.SerializerMethodField()

    class Meta:
        model = Expense
        fields = ["id", "number", "project", "stage", "amount", "date", "recipient", "purpose", "category", "note", "status",
                  "revision", "request_id", "document_url", "document_name", "document_type", "document_size",
                  "created_by", "created_at", "updated_at", "submitted_by", "submitted_at", "decided_by", "decided_at", "decision_reason"]
        read_only_fields = ["id", "number", "project", "status", "revision", "document_name", "document_type", "document_size",
                            "created_by", "created_at", "updated_at", "submitted_by", "submitted_at", "decided_by", "decided_at", "decision_reason"]
        extra_kwargs = {"recipient": {"required": False}, "purpose": {"required": False}, "category": {"required": False}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project = self.context.get("project")
        if project is not None:
            self.fields["stage"].queryset = project.stages.all()

    def get_document_url(self, expense):
        return reverse("expense-document", args=[expense.pk]) if expense.document else None

    def to_representation(self, expense):
        data = super().to_representation(expense)
        data["amount"] = f"{expense.amount:.2f}"
        return data


class ExpenseActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["submit", "withdraw", "confirm", "reject"])
    revision = serializers.IntegerField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
