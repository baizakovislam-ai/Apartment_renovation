from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.projects.models import ProjectMember
from apps.projects.views import accessible_project
from apps.quality.models import Remark
from apps.quality import services as quality
from apps.reports import services as reports
from . import task_services as tasks
from .models import Stage, Task


class TaskSerializer(serializers.ModelSerializer):
    stage = serializers.PrimaryKeyRelatedField(queryset=Stage.objects.none())
    assignee = serializers.PrimaryKeyRelatedField(queryset=ProjectMember.objects.none())
    version = serializers.IntegerField(required=False, min_value=1)
    request_id = serializers.UUIDField(required=False)

    class Meta:
        model = Task
        fields = ["id", "project", "stage", "name", "description", "assignee", "due_date", "status", "result", "version", "request_id", "created_by", "created_at", "updated_at"]
        read_only_fields = ["id", "project", "status", "result", "created_by", "created_at", "updated_at"]

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stage"].queryset = project.stages.all()
        self.fields["assignee"].queryset = project.members.filter(role="executor")


class TaskList(APIView):
    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        return Response(TaskSerializer(tasks.visible_tasks(project, request.user), many=True, project=project).data)

    def post(self, request, project_id):
        project = accessible_project(request.user, project_id)
        serializer = TaskSerializer(data=request.data, project=project)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop("version", None)
        task = tasks.create_task(project=project, actor=request.user, **data)
        return Response(TaskSerializer(task, project=project).data, status=201)


def accessible_task(user, task_id):
    task = get_object_or_404(Task, pk=task_id, project__members__user=user)
    return get_object_or_404(tasks.visible_tasks(task.project, user), pk=task.pk)


class TaskDetail(APIView):
    def get(self, request, task_id):
        task = accessible_task(request.user, task_id)
        return Response(TaskSerializer(task, project=task.project).data)

    def patch(self, request, task_id):
        task = accessible_task(request.user, task_id)
        serializer = TaskSerializer(task, data=request.data, partial=True, project=task.project)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        version = data.pop("version", None)
        data.pop("request_id", None)
        task = tasks.update_task(task=task, actor=request.user, version=version, **data)
        return Response(TaskSerializer(task, project=task.project).data)

    def delete(self, request, task_id):
        task = accessible_task(request.user, task_id)
        serializer = VersionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tasks.delete_task(task=task, actor=request.user, **serializer.validated_data)
        return Response(status=204)


class VersionSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)


class TaskActionSerializer(VersionSerializer):
    action = serializers.ChoiceField(choices=["start", "submit", "approve", "return"])
    result = serializers.CharField(required=False, allow_blank=True, max_length=10000)


class TaskAction(APIView):
    def post(self, request, task_id):
        task = accessible_task(request.user, task_id)
        serializer = TaskActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = tasks.transition_task(task=task, actor=request.user, **serializer.validated_data)
        return Response(TaskSerializer(task, project=task.project).data)


def report_data(report):
    return {"id": report.pk, "task": report.task_id, "description": report.description, "author": report.author_id, "created_at": report.created_at,
            "photos": [{"id": photo.pk, "url": reverse("workspace:photo", args=[photo.pk])} for photo in report.photos.all()]}


class ReportSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=10000)
    images = serializers.ListField(child=serializers.FileField(), min_length=1, max_length=8)
    request_id = serializers.UUIDField(required=False)


class TaskReports(APIView):
    def get(self, request, task_id):
        task = accessible_task(request.user, task_id)
        return Response([report_data(report) for report in task.photo_reports.prefetch_related("photos")])

    def post(self, request, task_id):
        task = accessible_task(request.user, task_id)
        serializer = ReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = reports.create_report(task=task, actor=request.user, **serializer.validated_data)
        return Response(report_data(report), status=201)


class RemarkSerializer(serializers.ModelSerializer):
    stage = serializers.PrimaryKeyRelatedField(queryset=Stage.objects.none())
    responsible = serializers.PrimaryKeyRelatedField(queryset=ProjectMember.objects.none())
    request_id = serializers.UUIDField(required=False)
    replies = serializers.SerializerMethodField()

    class Meta:
        model = Remark
        fields = ["id", "project", "stage", "description", "responsible", "due_date", "status", "version", "request_id", "created_by", "created_at", "closed_by", "closed_at", "replies"]
        read_only_fields = ["id", "project", "status", "version", "created_by", "created_at", "closed_by", "closed_at", "replies"]

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stage"].queryset = project.stages.all()
        self.fields["responsible"].queryset = project.members.filter(role__in=["executor", "foreman"])

    def get_replies(self, remark):
        return [{"id": reply.pk, "author": reply.author_id, "text": reply.text, "created_at": reply.created_at} for reply in remark.replies.all()]


class Remarks(APIView):
    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        return Response(RemarkSerializer(quality.visible_remarks(project, request.user), many=True, project=project).data)

    def post(self, request, project_id):
        project = accessible_project(request.user, project_id)
        serializer = RemarkSerializer(data=request.data, project=project)
        serializer.is_valid(raise_exception=True)
        remark = quality.create_remark(project=project, actor=request.user, **serializer.validated_data)
        return Response(RemarkSerializer(remark, project=project).data, status=201)


class RemarkActionSerializer(VersionSerializer):
    action = serializers.ChoiceField(choices=["fix", "close", "reopen", "reply"])
    text = serializers.CharField(required=False, allow_blank=True, max_length=10000)


class RemarkAction(APIView):
    def post(self, request, project_id, remark_id):
        project = accessible_project(request.user, project_id)
        remark = get_object_or_404(quality.visible_remarks(project, request.user), pk=remark_id)
        serializer = RemarkActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        remark = quality.remark_action(remark=remark, actor=request.user, **serializer.validated_data)
        return Response(RemarkSerializer(remark, project=project).data)


class AcceptanceSerializer(VersionSerializer):
    action = serializers.ChoiceField(choices=["submit", "accept", "return"])
    reason = serializers.CharField(required=False, allow_blank=True, max_length=10000)


class AcceptanceAction(APIView):
    def post(self, request, stage_id):
        stage = get_object_or_404(Stage, pk=stage_id, project__members__user=request.user)
        serializer = AcceptanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stage = quality.stage_action(stage=stage, actor=request.user, **serializer.validated_data)
        return Response({"id": stage.pk, "status": stage.status, "acceptance_version": stage.acceptance_version})


class Progress(APIView):
    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        progress = tasks.project_progress(project)
        if project.members.get(user=request.user).role == "executor":
            progress = {key: progress[key] for key in ["percent", "stages", "accepted"]}
        return Response(progress)
