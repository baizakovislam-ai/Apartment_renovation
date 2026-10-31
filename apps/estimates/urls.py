from django.urls import path
from . import views

urlpatterns = [
    path("projects/<int:project_id>/estimates/", views.EstimateListCreateView.as_view(), name="estimate-list"),
    path("estimates/<int:pk>/", views.EstimateDetailView.as_view(), name="estimate-detail"),
    path("estimates/<int:pk>/action/", views.EstimateActionView.as_view(), name="estimate-action"),
    path("estimates/<int:pk>/lines/", views.EstimateLineCreateView.as_view(), name="estimate-lines"),
    path("estimate-lines/<int:pk>/", views.EstimateLineDetailView.as_view(), name="estimate-line-detail"),
    path("projects/<int:project_id>/estimate-changes/", views.ChangeRequestListCreateView.as_view(), name="change-list"),
    path("estimate-changes/<int:pk>/", views.ChangeRequestDetailView.as_view(), name="change-detail"),
    path("estimate-changes/<int:pk>/items/", views.ChangeItemCreateView.as_view(), name="change-items"),
    path("estimate-changes/<int:pk>/action/", views.ChangeActionView.as_view(), name="change-action"),
    path("estimate-change-items/<int:pk>/", views.ChangeItemDetailView.as_view(), name="change-item-detail"),
    path("projects/<int:project_id>/start/", views.StartWorksView.as_view(), name="project-start"),
    path("projects/<int:project_id>/calculations/", views.CalculationListCreateView.as_view(), name="calculation-list"),
    path("calculations/<int:pk>/", views.CalculationDetailView.as_view(), name="calculation-detail"),
    path("calculations/<int:pk>/items/", views.CalculationItemCreateView.as_view(), name="calculation-items"),
    path("calculation-items/<int:pk>/", views.CalculationItemDetailView.as_view(), name="calculation-item-detail"),
    path("calculations/<int:pk>/to-estimate/", views.CalculationToEstimateView.as_view(), name="calculation-to-estimate"),
    path("projects/<int:project_id>/approvals/", views.ApprovalListView.as_view(), name="approval-list"),
    path("projects/<int:project_id>/search/", views.ProjectSearchView.as_view(), name="project-search"),
]
