from django.urls import path
from . import views

urlpatterns = [
    path("projects/<int:project_id>/remarks/", views.RemarkListCreateView.as_view(), name="remark-list"),
    path("remarks/<int:pk>/", views.RemarkDetailView.as_view(), name="remark-detail"),
    path("remarks/<int:pk>/action/", views.RemarkActionView.as_view(), name="remark-action"),
    path("stages/<int:pk>/acceptance/", views.StageAcceptanceView.as_view(), name="stage-acceptance"),
]
