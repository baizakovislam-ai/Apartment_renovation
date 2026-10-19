from django.urls import path
from . import views

urlpatterns = [
    path("projects/", views.ProjectListCreateView.as_view(), name="project-list"),
    path("projects/<int:pk>/", views.ProjectDetailView.as_view(), name="project-detail"),
    path("projects/<int:project_id>/members/", views.MemberListView.as_view(), name="member-list"),
    path("projects/<int:project_id>/members/<int:member_id>/", views.MemberDetailView.as_view(), name="member-detail"),
    path("projects/<int:project_id>/invitations/", views.InvitationListCreateView.as_view(), name="invitation-list"),
    path("projects/<int:project_id>/invitations/<int:invitation_id>/cancel/", views.InvitationCancelView.as_view(), name="invitation-cancel"),
    path("invitations/<str:token>/accept/", views.InvitationAcceptView.as_view(), name="invitation-accept"),
    path("projects/<int:project_id>/events/", views.EventListView.as_view(), name="project-events"),
]
