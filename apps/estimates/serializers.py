from rest_framework import serializers

from apps.repairs.models import Stage
from .models import Calculation, CalculationItem, ChangeItem, ChangeRequest, CostType, Estimate, EstimateLine


class EstimateLineSerializer(serializers.ModelSerializer):
    stage = serializers.PrimaryKeyRelatedField(queryset=Stage.objects.none())
    stage_name = serializers.CharField(source="stage.name", read_only=True)
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    cost_type = serializers.ChoiceField(choices=CostType.choices, required=False)

    class Meta:
        model = EstimateLine
        fields = ["id", "stage", "stage_name", "name", "cost_type", "unit", "quantity", "price", "amount", "position"]
        read_only_fields = ["id", "position"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project = self.context.get("project")
        if project is not None:
            self.fields["stage"].queryset = project.stages.all()


class EstimateSerializer(serializers.ModelSerializer):
    lines = EstimateLineSerializer(many=True, read_only=True)

    class Meta:
        model = Estimate
        fields = ["id", "project", "number", "status", "total", "revision", "comment", "based_on", "created_by", "created_at",
                  "submitted_by", "submitted_at", "decided_by", "decided_at", "decision_reason", "lines"]
        read_only_fields = fields


class EstimateActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["submit", "withdraw", "approve", "reject"])
    comment = serializers.CharField(required=False, allow_blank=True, default="")
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class CalculationItemSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    cost_type = serializers.ChoiceField(choices=CostType.choices, required=False)

    class Meta:
        model = CalculationItem
        fields = ["id", "work", "cost_type", "unit", "volume", "price", "amount", "position"]
        read_only_fields = ["id", "position"]


class CalculationSerializer(serializers.ModelSerializer):
    items = CalculationItemSerializer(many=True, read_only=True)
    total = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)

    class Meta:
        model = Calculation
        fields = ["id", "project", "name", "note", "total", "items", "created_by", "created_at", "updated_at"]
        read_only_fields = ["id", "project", "total", "items", "created_by", "created_at", "updated_at"]


# ---------- changes of the approved estimate (FR11) ----------

class ChangeItemSerializer(serializers.ModelSerializer):
    stage_name = serializers.CharField(source="stage.name", read_only=True)
    old_amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    new_amount = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    delta = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)

    class Meta:
        model = ChangeItem
        fields = ["id", "action", "line", "stage", "stage_name", "name", "cost_type", "unit", "quantity", "price",
                  "old_amount", "new_amount", "delta"]
        read_only_fields = fields


class ChangeRequestSerializer(serializers.ModelSerializer):
    items = ChangeItemSerializer(many=True, read_only=True)
    cost_delta = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    old_total = serializers.DecimalField(source="estimate.total", max_digits=16, decimal_places=2, read_only=True)
    new_total = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    current_end_date = serializers.DateField(read_only=True)
    days_delta = serializers.IntegerField(read_only=True)

    class Meta:
        model = ChangeRequest
        fields = ["id", "project", "number", "status", "estimate", "result_estimate", "reason", "schedule_stage", "new_end_date",
                  "current_end_date", "days_delta", "old_total", "cost_delta", "new_total", "revision", "created_by", "created_at",
                  "submitted_by", "submitted_at", "decided_by", "decided_at", "decision_reason", "items"]
        read_only_fields = fields


class ChangeRequestWriteSerializer(serializers.Serializer):
    reason = serializers.CharField(allow_blank=True)
    schedule_stage = serializers.PrimaryKeyRelatedField(queryset=Stage.objects.none(), required=False, allow_null=True, default=None)
    new_end_date = serializers.DateField(required=False, allow_null=True, default=None)
    revision = serializers.IntegerField(required=False)

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        if project is not None:
            self.fields["schedule_stage"].queryset = project.stages.all()


class ChangeItemWriteSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=ChangeItem.Action.choices)
    line = serializers.PrimaryKeyRelatedField(queryset=EstimateLine.objects.none(), required=False, allow_null=True, default=None)
    stage = serializers.PrimaryKeyRelatedField(queryset=Stage.objects.none(), required=False)
    name = serializers.CharField(required=False, allow_blank=True)
    cost_type = serializers.ChoiceField(choices=CostType.choices, required=False)
    unit = serializers.CharField(required=False, allow_blank=True)
    quantity = serializers.CharField(required=False)
    price = serializers.CharField(required=False)
    revision = serializers.IntegerField()

    def __init__(self, *args, change=None, **kwargs):
        super().__init__(*args, **kwargs)
        if change is not None:
            self.fields["line"].queryset = change.estimate.lines.all()
            self.fields["stage"].queryset = change.project.stages.all()


class ChangeActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["submit", "withdraw", "approve", "reject"])
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    revision = serializers.IntegerField(required=False)
