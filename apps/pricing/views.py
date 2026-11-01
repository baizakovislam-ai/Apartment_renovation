from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.estimates.models import Calculation
from apps.estimates.serializers import CalculationItemSerializer
from apps.estimates.views import finance_object, finance_project
from . import services
from .models import PriceItem
from .serializers import AddToCalculationSerializer, PriceItemSerializer


def item_data(request):
    return {key: request.data[key] for key in services.FIELDS if key in request.data}


class PriceListView(APIView):
    """GET own price list (?q=, ?category=); POST {name, category, unit, price} adds a position."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        services.require_foreman(request.user)
        items = services.filter_items(request.user.price_items.all(), request.query_params)
        return Response(PriceItemSerializer(items, many=True).data)

    def post(self, request):
        item = services.add_price_item(actor=request.user, **item_data(request))
        return Response(PriceItemSerializer(item).data, status=status.HTTP_201_CREATED)


class PriceItemDetailView(APIView):
    """GET, PATCH {name?, category?, unit?, price?}, DELETE one own position; other people's positions are 404."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        return Response(PriceItemSerializer(get_object_or_404(request.user.price_items, pk=pk)).data)

    def patch(self, request, pk):
        item = services.update_price_item(item=get_object_or_404(request.user.price_items, pk=pk), actor=request.user, **item_data(request))
        return Response(PriceItemSerializer(item).data)

    def delete(self, request, pk):
        services.delete_price_item(item=get_object_or_404(request.user.price_items, pk=pk), actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProjectPriceListView(APIView):
    """GET the price list of the project's foreman — for the customer and the foreman."""
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = finance_project(request, project_id)
        items = services.filter_items(services.project_price_list(project), request.query_params)
        return Response(PriceItemSerializer(items, many=True).data)


class CalculationFromPriceListView(APIView):
    """POST {price_item, volume} adds a work from the foreman's price list to the calculation."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        calculation, project = finance_object(request, Calculation, pk, "project")
        serializer = AddToCalculationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        price_item = PriceItem.objects.filter(pk=serializer.validated_data["price_item"]).first()
        item = services.add_to_calculation(calculation=calculation, actor=request.user, price_item=price_item,
                                           volume=serializer.validated_data["volume"])
        return Response(CalculationItemSerializer(item).data, status=status.HTTP_201_CREATED)
