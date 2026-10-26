from rest_framework import serializers
from apps.projects.models import ProjectMember
from .models import Stage


class StageSerializer(serializers.ModelSerializer):
    responsible = serializers.PrimaryKeyRelatedField(queryset=ProjectMember.objects.none(), required=False, allow_null=True)

    class Meta:
        model = Stage
        fields = ["id", "project", "name", "description", "start_date", "end_date", "responsible", "position", "status", "acceptance_version", "created_by", "created_at", "updated_at"]
        read_only_fields = ["id", "project", "acceptance_version", "created_by", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project = self.context.get("project")
        if project is not None:
            self.fields["responsible"].queryset = project.members.all()
