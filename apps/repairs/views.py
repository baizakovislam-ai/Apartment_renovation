from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import APIException
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.projects.views import accessible_project
from . import services, task_services
from .models import Photo, Stage, Task
from .serializers import (PhotoReportCreateSerializer, PhotoReportSerializer, StageSerializer, TaskActionSerializer,
                          TaskSerializer)


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


def filter_tasks(tasks, params):
    """Search and filters shared by the API and the web pages (ТЗ FR17)."""
    query = (params.get("q") or "").strip()
    if query:
        tasks = tasks.filter(Q(name__icontains=query) | Q(description__icontains=query))
    if params.get("status") in Task.Status.values:
        tasks = tasks.filter(status=params["status"])
    if str(params.get("stage") or "").isdigit():
        tasks = tasks.filter(stage_id=params["stage"])
    if str(params.get("assignee") or "").isdigit():
        tasks = tasks.filter(assignee_id=params["assignee"])
    if params.get("deadline") == "overdue":
        tasks = tasks.filter(due_date__lt=timezone.localdate()).exclude(status=Task.Status.DONE)
    return tasks


class TaskListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        tasks = filter_tasks(task_services.visible_tasks(project, request.user), request.query_params)
        return Response(TaskSerializer(tasks, many=True, context={"project": project}).data)

    def post(self, request, project_id):
        project = accessible_project(request.user, project_id)
        serializer = TaskSerializer(data=request.data, context={"project": project})
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop("version", None)
        task = task_services.create_task(project=project, actor=request.user, **data)
        return Response(TaskSerializer(task, context={"project": project}).data, status=status.HTTP_201_CREATED)


def accessible_task(user, pk):
    task = get_object_or_404(Task.objects.filter(project__members__user=user).select_related("project", "stage", "assignee__user"), pk=pk)
    task_services.require_task_access(task, user)
    return task


class TaskDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        task = accessible_task(request.user, pk)
        return Response(TaskSerializer(task, context={"project": task.project}).data)

    def patch(self, request, pk):
        task = accessible_task(request.user, pk)
        serializer = TaskSerializer(task, data=request.data, partial=True, context={"project": task.project})
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop("request_id", None)
        task = task_services.update_task(task=task, actor=request.user, version=data.pop("version", None), **data)
        return Response(TaskSerializer(task, context={"project": task.project}).data)

    def delete(self, request, pk):
        task = accessible_task(request.user, pk)
        task_services.delete_task(task=task, actor=request.user, version=request.data.get("version") or request.query_params.get("version"))
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        task = accessible_task(request.user, pk)
        serializer = TaskActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = task_services.task_action(task=task, actor=request.user, **serializer.validated_data)
        return Response(TaskSerializer(task, context={"project": task.project}).data)


class PhotoReportListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request, pk):
        task = accessible_task(request.user, pk)
        reports = task.reports.select_related("author").prefetch_related("photos")
        return Response(PhotoReportSerializer(reports, many=True).data)

    def post(self, request, pk):
        task = accessible_task(request.user, pk)
        data = {"description": request.data.get("description", ""), "images": request.FILES.getlist("images")}
        if request.data.get("request_id"):
            data["request_id"] = request.data["request_id"]
        serializer = PhotoReportCreateSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        report = task_services.add_photo_report(task=task, actor=request.user, **serializer.validated_data)
        return Response(PhotoReportSerializer(report).data, status=status.HTTP_201_CREATED)


class ProgressView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        progress = task_services.project_progress(project)
        progress["stages"] = [{"id": stage.pk, "name": stage.name, "status": stage.status, "percent": percent} for stage, percent in progress["stages"]]
        return Response(progress)


@login_required
def photo_file(request, pk):
    """Serves a photo only to members who may see its task; a direct URL gives strangers 404."""
    photo = get_object_or_404(Photo.objects.select_related("report__task__project"), pk=pk, report__project__members__user=request.user)
    try:
        task_services.require_task_access(photo.report.task, request.user)
    except APIException:
        raise Http404
    response = FileResponse(photo.file.open("rb"), content_type=photo.content_type)
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response
