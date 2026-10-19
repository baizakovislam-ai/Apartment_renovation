from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from apps.projects.views import accessible_project
from . import services
from .models import Stage
from .serializers import StageSerializer


class StageListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = StageSerializer

    def get_project(self):
        if not hasattr(self, "project"):
            self.project = accessible_project(self.request.user, self.kwargs["project_id"])
        return self.project

    def get_queryset(self):
        return self.get_project().stages.select_related("responsible")

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "project": self.get_project()}

    def perform_create(self, serializer):
        serializer.instance = services.create_stage(project=self.get_project(), actor=self.request.user, **serializer.validated_data)


class StageDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = StageSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return Stage.objects.filter(project__members__user=self.request.user).select_related("project", "responsible")

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "project": self.get_object().project}

    def perform_update(self, serializer):
        serializer.instance = services.update_stage(stage=self.get_object(), actor=self.request.user, **serializer.validated_data)

    def perform_destroy(self, instance):
        services.delete_stage(stage=instance, actor=self.request.user)
