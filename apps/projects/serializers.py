from rest_framework import serializers
from .models import Project, ProjectEvent, ProjectInvitation, ProjectMember


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ["id", "name", "address", "area", "start_date", "end_date", "budget", "description", "status", "archived_at", "archive_reason", "created_by", "created_at", "updated_at"]
        read_only_fields = ["id", "status", "archived_at", "archive_reason", "created_by", "created_at", "updated_at"]


class MemberSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.get_username", read_only=True)

    class Meta:
        model = ProjectMember
        fields = ["id", "project", "user", "username", "role", "joined_at", "is_active", "removed_at"]
        read_only_fields = fields


class MemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["foreman", "executor"])


class InvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=["foreman", "executor"])


class InvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectInvitation
        fields = ["id", "project", "email", "role", "token", "status", "invited_by", "created_at", "expires_at", "accepted_at"]
        read_only_fields = fields


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectEvent
        fields = ["id", "actor", "action", "data", "created_at"]
        read_only_fields = fields
