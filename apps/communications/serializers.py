from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    is_read = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = ["id", "project", "project_name", "kind", "title", "text", "url", "created_at", "is_read", "read_at", "emailed_at"]
