from django.urls import path
from . import work_views as views

urlpatterns = [
    path("projects/<int:project_id>/tasks/", views.task_list, name="tasks"),
    path("projects/<int:project_id>/tasks/new/", views.task_form, name="task-create"),
    path("projects/<int:project_id>/tasks/<int:task_id>/", views.task_detail, name="task-detail"),
    path("projects/<int:project_id>/tasks/<int:task_id>/edit/", views.task_form, name="task-edit"),
    path("projects/<int:project_id>/tasks/<int:task_id>/action/", views.task_action, name="task-action"),
    path("projects/<int:project_id>/photo-reports/", views.report_list, name="reports"),
    path("private/photos/<int:photo_id>/", views.photo, name="photo"),
    path("projects/<int:project_id>/acceptance/", views.acceptance, name="acceptance"),
    path("projects/<int:project_id>/acceptance/<int:stage_id>/", views.acceptance_action, name="acceptance-action"),
    path("projects/<int:project_id>/remarks/", views.remarks, name="remarks"),
    path("projects/<int:project_id>/remarks/new/", views.remark_create, name="remark-create"),
    path("projects/<int:project_id>/remarks/<int:remark_id>/", views.remark_action, name="remark-action"),
]
