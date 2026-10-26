from django.urls import path
from . import views, work_views as work

app_name = "workspace"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("projects/", views.project_list, name="projects"),
    path("projects/new/", views.project_create, name="project-create"),
    path("projects/<int:project_id>/stages/", views.stage_list, name="stages"),
    path("projects/<int:project_id>/stages/new/", views.stage_form, name="stage-create"),
    path("projects/<int:project_id>/stages/<int:stage_id>/edit/", views.stage_form, name="stage-edit"),
    path("projects/<int:project_id>/stages/<int:stage_id>/delete/", views.stage_delete, name="stage-delete"),
    path("projects/<int:project_id>/members/", views.members, name="members"),
    path("projects/<int:project_id>/members/<int:member_id>/", views.member_action, name="member-action"),
    path("projects/<int:project_id>/invitations/<int:invitation_id>/cancel/", views.invitation_cancel, name="invitation-cancel"),
    path("invitations/<str:token>/", views.accept, name="accept"),
    path("projects/<int:project_id>/history/", views.history, name="history"),
    path("projects/<int:project_id>/", work.project_detail, name="project"),
    path("projects/<int:project_id>/edit/", work.project_edit, name="project-edit"),
    path("projects/<int:project_id>/delete/", work.project_delete, name="project-delete"),
    path("projects/<int:project_id>/stages/<int:stage_id>/start/", work.stage_start, name="stage-start"),
    path("projects/<int:project_id>/tasks/", work.task_list, name="tasks"),
    path("projects/<int:project_id>/tasks/new/", work.task_form, name="task-create"),
    path("projects/<int:project_id>/tasks/<int:task_id>/", work.task_detail, name="task"),
    path("projects/<int:project_id>/tasks/<int:task_id>/edit/", work.task_form, name="task-edit"),
    path("projects/<int:project_id>/tasks/<int:task_id>/delete/", work.task_delete, name="task-delete"),
    path("projects/<int:project_id>/tasks/<int:task_id>/action/", work.task_action, name="task-action"),
    path("projects/<int:project_id>/tasks/<int:task_id>/reports/", work.photo_report_create, name="photo-report-create"),
    path("projects/<int:project_id>/acceptance/", work.acceptance, name="acceptance"),
    path("projects/<int:project_id>/stages/<int:stage_id>/acceptance/", work.stage_acceptance_action, name="stage-acceptance"),
    path("projects/<int:project_id>/remarks/", work.remarks, name="remarks"),
    path("projects/<int:project_id>/remarks/<int:remark_id>/", work.remark_action, name="remark-action"),
]
