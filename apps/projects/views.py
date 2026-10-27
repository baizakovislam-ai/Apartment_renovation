from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .emails import send_invitation_email
from .models import Project
from .permissions import require_manager, require_member
from .serializers import (EventSerializer, InvitationCreateSerializer, InvitationSerializer,
                          MemberRoleSerializer, MemberSerializer, ProjectSerializer)


def member_projects(user):
    """Projects where the user is a current (not removed) member."""
    return Project.objects.filter(members__user=user, members__is_active=True)


def accessible_project(user, project_id):
    return get_object_or_404(member_projects(user), pk=project_id)


def invitation_url(request, invitation):
    return request.build_absolute_uri(reverse("workspace:accept", args=[invitation.token]))


class ProjectListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectSerializer

    def get_queryset(self):
        projects = member_projects(self.request.user)
        return projects if self.request.query_params.get("archived") else projects.filter(archived_at__isnull=True)

    def perform_create(self, serializer):
        serializer.instance = services.create_project(user=self.request.user, **serializer.validated_data)


class ProjectDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return member_projects(self.request.user)

    def perform_update(self, serializer):
        serializer.instance = services.update_project(project=self.get_object(), actor=self.request.user, **serializer.validated_data)

    def perform_destroy(self, instance):
        services.delete_project(project=instance, actor=self.request.user)


class ProjectArchiveView(APIView):
    """POST {"reason": "..."} archives the project; DELETE restores it."""
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id):
        project = services.archive_project(project=accessible_project(request.user, project_id), actor=request.user, reason=request.data.get("reason", ""))
        return Response(ProjectSerializer(project).data)

    def delete(self, request, project_id):
        project = services.restore_project(project=accessible_project(request.user, project_id), actor=request.user)
        return Response(ProjectSerializer(project).data)


class MemberListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        members = project.members.select_related("user")
        if not request.query_params.get("all"):
            members = members.filter(is_active=True)
        return Response(MemberSerializer(members, many=True).data)


class MemberDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, project_id, member_id):
        project = accessible_project(request.user, project_id)
        serializer = MemberRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = services.change_member_role(project=project, actor=request.user, member_id=member_id, **serializer.validated_data)
        return Response(MemberSerializer(member).data)

    def delete(self, request, project_id, member_id):
        """Removes the member; unfinished work goes to ?replacement=<member id> (or "replacement" in the body)."""
        project = accessible_project(request.user, project_id)
        replacement = request.data.get("replacement") or request.query_params.get("replacement")
        services.remove_member(project=project, actor=request.user, member_id=member_id, replacement_id=replacement or None)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        member = require_manager(project, request.user)
        invitations = project.invitations.all()
        if member.role == "foreman":
            invitations = invitations.filter(role="executor")
        # Expiry is displayed immediately without mutating data on GET.
        data = InvitationSerializer(invitations, many=True).data
        for invitation, item in zip(invitations, data):
            if invitation.status == "pending" and invitation.expires_at <= timezone.now():
                item["status"] = "expired"
        return Response(data)

    def post(self, request, project_id):
        project = accessible_project(request.user, project_id)
        serializer = InvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = services.invite_member(project=project, actor=request.user, **serializer.validated_data)
        data = InvitationSerializer(invitation).data
        data["accept_url"] = invitation_url(request, invitation)
        data["email_sent"] = send_invitation_email(invitation, data["accept_url"])
        return Response(data, status=status.HTTP_201_CREATED)


class InvitationCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, invitation_id):
        project = accessible_project(request.user, project_id)
        services.cancel_invitation(project=project, actor=request.user, invitation_id=invitation_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyInvitationsView(APIView):
    """Pending invitations addressed to the signed-in user's email."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(InvitationSerializer(services.pending_invitations_for(request.user), many=True).data)


class InvitationAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, token):
        member = services.accept_invitation(token=token, user=request.user)
        return Response(MemberSerializer(member).data, status=status.HTTP_201_CREATED)


class InvitationDeclineView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, token):
        services.decline_invitation(token=token, user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EventListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        require_member(project, request.user, ["customer", "foreman"])
        return Response(EventSerializer(project.events.all(), many=True).data)
