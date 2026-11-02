from django.urls import path
from . import views

urlpatterns = [
    path("projects/<int:project_id>/expenses/", views.ExpenseListCreateView.as_view(), name="expense-list"),
    path("projects/<int:project_id>/finance/", views.FinanceSummaryView.as_view(), name="finance-summary"),
    path("expenses/<int:pk>/", views.ExpenseDetailView.as_view(), name="expense-detail"),
    path("expenses/<int:pk>/action/", views.ExpenseActionView.as_view(), name="expense-action"),
]
