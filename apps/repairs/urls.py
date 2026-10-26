from django.urls import path
from . import views
from .workflow_urls import urlpatterns as workflow_patterns

urlpatterns = [
    path("projects/<int:project_id>/stages/", views.StageListCreateView.as_view(), name="stage-list"),
    path("stages/<int:pk>/", views.StageDetailView.as_view(), name="stage-detail"),
]
urlpatterns += workflow_patterns
