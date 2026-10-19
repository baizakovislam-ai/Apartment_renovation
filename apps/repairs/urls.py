from django.urls import path
from . import views

urlpatterns = [
    path("projects/<int:project_id>/stages/", views.StageListCreateView.as_view(), name="stage-list"),
    path("stages/<int:pk>/", views.StageDetailView.as_view(), name="stage-detail"),
]
