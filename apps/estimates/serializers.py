from rest_framework import serializers

from apps.repairs.models import Stage
from .models import Calculation, CalculationItem, CostType, Estimate, EstimateLine


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
