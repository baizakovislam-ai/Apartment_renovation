from rest_framework import serializers
from .models import Project, ProjectEvent, ProjectInvitation, ProjectMember, SavedContact


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ["id", "name", "address", "description", "created_by", "created_at"]
        read_only_fields = ["id", "created_by", "created_at"]


class MemberSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.get_username", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = ProjectMember
        fields = ["id", "project", "user", "username", "full_name", "email", "role", "joined_at"]
        read_only_fields = fields


class MemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["foreman", "executor"])


class MemberAddSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False)
    contact_id = serializers.IntegerField(required=False, min_value=1)
    role = serializers.ChoiceField(choices=["foreman", "executor"], default="executor")

    def validate(self, attrs):
        if bool(attrs.get("email")) == bool(attrs.get("contact_id")):
            raise serializers.ValidationError("Укажите email или contact_id — один вариант.")
        return attrs


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedContact
        fields = ["id", "email", "name", "last_used_at"]
        read_only_fields = fields


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
