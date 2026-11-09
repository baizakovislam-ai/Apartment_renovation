from django.urls import path
from . import views

urlpatterns = [
    path("price-list/", views.PriceListView.as_view(), name="price-list"),
    path("price-list/<int:pk>/", views.PriceItemDetailView.as_view(), name="price-item-detail"),
    path("projects/<int:project_id>/price-list/average/", views.ProjectAveragePriceView.as_view(), name="project-average-price"),
    path("projects/<int:project_id>/price-list/", views.ProjectPriceListView.as_view(), name="project-price-list"),
    path("calculations/<int:pk>/from-price-list/", views.CalculationFromPriceListView.as_view(), name="calculation-from-price-list"),
]
