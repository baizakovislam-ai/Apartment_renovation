from rest_framework import serializers

from .models import PriceItem, WorkCategory


class PriceItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="get_category_display", read_only=True)

    class Meta:
        model = PriceItem
        fields = ["id", "foreman", "name", "category", "category_name", "unit", "price", "created_at", "updated_at"]
        read_only_fields = fields


class AddToCalculationSerializer(serializers.Serializer):
    price_item = serializers.IntegerField()
    volume = serializers.CharField()


class AveragePriceQuerySerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, trim_whitespace=True)
    category = serializers.ChoiceField(choices=WorkCategory.choices)
    unit = serializers.CharField(max_length=20, trim_whitespace=True)
