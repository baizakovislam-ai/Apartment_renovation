from django.urls import path
from . import views

urlpatterns = [
    path("projects/<int:project_id>/stages/", views.StageListCreateView.as_view(), name="stage-list"),
    path("stages/<int:pk>/", views.StageDetailView.as_view(), name="stage-detail"),
    path("projects/<int:project_id>/tasks/", views.TaskListCreateView.as_view(), name="task-list"),
    path("tasks/<int:pk>/", views.TaskDetailView.as_view(), name="task-detail"),
    path("tasks/<int:pk>/action/", views.TaskActionView.as_view(), name="task-action"),
    path("tasks/<int:pk>/reports/", views.PhotoReportListCreateView.as_view(), name="task-reports"),
    path("projects/<int:project_id>/progress/", views.ProgressView.as_view(), name="project-progress"),
]
