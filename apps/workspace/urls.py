from django.urls import path
from . import views
from .work_urls import urlpatterns as work_patterns

app_name = "workspace"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("projects/", views.project_list, name="projects"),
    path("projects/new/", views.project_create, name="project-create"),
    path("projects/<int:project_id>/", views.project_detail, name="project-detail"),
    path("projects/<int:project_id>/edit/", views.project_edit, name="project-edit"),
    path("projects/<int:project_id>/stages/quick/", views.quick_stage, name="stage-quick"),
    path("projects/<int:project_id>/stages/<int:stage_id>/action/", views.stage_action, name="stage-action"),
    path("projects/<int:project_id>/stages/", views.stage_list, name="stages"),
    path("projects/<int:project_id>/stages/new/", views.stage_form, name="stage-create"),
    path("projects/<int:project_id>/stages/<int:stage_id>/edit/", views.stage_form, name="stage-edit"),
    path("projects/<int:project_id>/stages/<int:stage_id>/delete/", views.stage_delete, name="stage-delete"),
    path("projects/<int:project_id>/members/", views.members, name="members"),
    path("projects/<int:project_id>/members/<int:member_id>/", views.member_action, name="member-action"),
    path("projects/<int:project_id>/invitations/<int:invitation_id>/cancel/", views.invitation_cancel, name="invitation-cancel"),
    path("invitations/<str:token>/", views.accept, name="accept"),
    path("projects/<int:project_id>/history/", views.history, name="history"),
]
urlpatterns += work_patterns
