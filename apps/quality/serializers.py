from rest_framework import serializers

from apps.projects.models import ProjectMember
from apps.repairs.models import Stage, Task
from .models import Remark, RemarkComment, StageDecision


class RemarkCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = RemarkComment
        fields = ["id", "author", "text", "action", "created_at"]
        read_only_fields = fields


class RemarkSerializer(serializers.ModelSerializer):
    stage = serializers.PrimaryKeyRelatedField(queryset=Stage.objects.none())
    task = serializers.PrimaryKeyRelatedField(queryset=Task.objects.none(), required=False, allow_null=True)
    responsible = serializers.PrimaryKeyRelatedField(queryset=ProjectMember.objects.none())
    request_id = serializers.UUIDField(required=False, write_only=True)
    comments = RemarkCommentSerializer(many=True, read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = Remark
        fields = ["id", "project", "stage", "task", "description", "responsible", "due_date", "status", "is_overdue", "version",
                  "request_id", "created_by", "created_at", "closed_by", "closed_at", "comments"]
        read_only_fields = ["id", "project", "status", "version", "created_by", "created_at", "closed_by", "closed_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project = self.context.get("project")
        if project is not None:
            self.fields["stage"].queryset = project.stages.all()
            self.fields["task"].queryset = project.tasks.all()
            self.fields["responsible"].queryset = project.members.all()


class RemarkActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["reply", "fix", "close", "reopen"])
    version = serializers.IntegerField(required=False)
    text = serializers.CharField(required=False, allow_blank=True, default="")


class AcceptanceSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["submit", "accept", "return"])
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class StageDecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = StageDecision
        fields = ["id", "stage", "actor", "action", "reason", "created_at"]
        read_only_fields = fields
