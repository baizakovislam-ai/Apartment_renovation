from rest_framework import serializers

from .models import PriceItem


class PriceItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="get_category_display", read_only=True)

    class Meta:
        model = PriceItem
        fields = ["id", "foreman", "name", "category", "category_name", "unit", "price", "created_at", "updated_at"]
        read_only_fields = fields


class AddToCalculationSerializer(serializers.Serializer):
    price_item = serializers.IntegerField()
    volume = serializers.CharField()
