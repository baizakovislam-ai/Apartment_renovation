from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Project
from .permissions import require_customer, require_member
from .serializers import (EventSerializer, InvitationCreateSerializer, InvitationSerializer,
                          MemberRoleSerializer, MemberSerializer, ProjectSerializer)


def accessible_project(user, project_id):
    return get_object_or_404(Project.objects.filter(members__user=user), pk=project_id)


class ProjectListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectSerializer

    def get_queryset(self):
        return Project.objects.filter(members__user=self.request.user)

    def perform_create(self, serializer):
        serializer.instance = services.create_project(user=self.request.user, **serializer.validated_data)


class ProjectDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectSerializer

    def get_queryset(self):
        return Project.objects.filter(members__user=self.request.user)


class MemberListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        return Response(MemberSerializer(project.members.select_related("user"), many=True).data)


class MemberDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, project_id, member_id):
        project = accessible_project(request.user, project_id)
        serializer = MemberRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = services.change_member_role(project=project, actor=request.user, member_id=member_id, **serializer.validated_data)
        return Response(MemberSerializer(member).data)

    def delete(self, request, project_id, member_id):
        project = accessible_project(request.user, project_id)
        services.remove_member(project=project, actor=request.user, member_id=member_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        require_customer(project, request.user)
        invitations = project.invitations.all()
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
        return Response(InvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)


class InvitationCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, invitation_id):
        project = accessible_project(request.user, project_id)
        services.cancel_invitation(project=project, actor=request.user, invitation_id=invitation_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitationAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, token):
        member = services.accept_invitation(token=token, user=request.user)
        return Response(MemberSerializer(member).data, status=status.HTTP_201_CREATED)


class EventListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        require_member(project, request.user, ["customer", "foreman"])
        return Response(EventSerializer(project.events.all(), many=True).data)
