from django.urls import reverse
from rest_framework import serializers

from apps.projects.models import ProjectMember
from .models import PhotoReport, Stage, Task


class StageSerializer(serializers.ModelSerializer):
    responsible = serializers.PrimaryKeyRelatedField(queryset=ProjectMember.objects.none())

    class Meta:
        model = Stage
        fields = ["id", "project", "name", "description", "start_date", "end_date", "responsible", "position", "status", "created_by", "created_at", "updated_at"]
        read_only_fields = ["id", "project", "created_by", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project = self.context.get("project")
        if project is not None:
            self.fields["responsible"].queryset = project.members.filter(is_active=True)


class TaskSerializer(serializers.ModelSerializer):
    """Writes accept stage/assignee ids; ownership is checked again in the service layer."""

    stage = serializers.PrimaryKeyRelatedField(queryset=Stage.objects.none())
    assignee = serializers.PrimaryKeyRelatedField(queryset=ProjectMember.objects.none())
    assignee_name = serializers.SerializerMethodField()
    is_overdue = serializers.BooleanField(read_only=True)
    request_id = serializers.UUIDField(required=False, write_only=True)
    version = serializers.IntegerField(required=False)

    class Meta:
        model = Task
        fields = ["id", "project", "stage", "name", "description", "assignee", "assignee_name", "due_date", "status", "result",
                  "return_reason", "is_overdue", "version", "request_id", "created_by", "created_at", "updated_at", "completed_at"]
        read_only_fields = ["id", "project", "status", "result", "return_reason", "created_by", "created_at", "updated_at", "completed_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project = self.context.get("project")
        if project is not None:
            self.fields["stage"].queryset = project.stages.all()
            self.fields["assignee"].queryset = project.members.filter(is_active=True)

    def get_assignee_name(self, task):
        return task.assignee.user.get_full_name() or task.assignee.user.get_username()


class TaskActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["start", "submit", "approve", "return"])
    version = serializers.IntegerField()
    text = serializers.CharField(required=False, allow_blank=True, default="")


class PhotoReportSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()

    class Meta:
        model = PhotoReport
        fields = ["id", "task", "author", "author_name", "description", "created_at", "photos"]
        read_only_fields = fields

    def get_author_name(self, report):
        return report.author.get_full_name() or report.author.get_username()

    def get_photos(self, report):
        # Only the access-checked URL is exposed, never the storage path.
        return [{"id": photo.pk, "url": reverse("photo-file", args=[photo.pk]), "width": photo.width, "height": photo.height,
                 "content_type": photo.content_type} for photo in report.photos.all()]


class PhotoReportCreateSerializer(serializers.Serializer):
    description = serializers.CharField()
    images = serializers.ListField(child=serializers.FileField(), allow_empty=False)
    request_id = serializers.UUIDField(required=False)
