from django.urls import path
from . import workflow_api as views

urlpatterns = [
    path("projects/<int:project_id>/tasks/", views.TaskList.as_view()),
    path("tasks/<int:task_id>/", views.TaskDetail.as_view()),
    path("tasks/<int:task_id>/action/", views.TaskAction.as_view()),
    path("tasks/<int:task_id>/reports/", views.TaskReports.as_view()),
    path("projects/<int:project_id>/remarks/", views.Remarks.as_view()),
    path("projects/<int:project_id>/remarks/<int:remark_id>/action/", views.RemarkAction.as_view()),
    path("stages/<int:stage_id>/acceptance/", views.AcceptanceAction.as_view()),
    path("projects/<int:project_id>/progress/", views.Progress.as_view()),
]
