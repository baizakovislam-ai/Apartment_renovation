from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.projects.permissions import require_manager
from apps.projects.views import accessible_project
from apps.repairs.models import Stage
from apps.repairs.serializers import StageSerializer
from . import services
from .models import Remark, RemarkPhoto
from .serializers import AcceptanceSerializer, RemarkActionSerializer, RemarkSerializer, StageDecisionSerializer


def filter_remarks(remarks, params, user=None):
    """Search and filters for remarks (ТЗ FR17)."""
    status_filter = params.get("status")
    if status_filter == "active":
        remarks = remarks.exclude(status=Remark.Status.CLOSED)
    elif status_filter in Remark.Status.values:
        remarks = remarks.filter(status=status_filter)
    if str(params.get("stage") or "").isdigit():
        remarks = remarks.filter(stage_id=params["stage"])
    query = (params.get("q") or "").strip()
    if query:
        remarks = remarks.filter(Q(description__icontains=query) | Q(task__name__icontains=query))
    if params.get("mine") and user is not None:
        remarks = remarks.filter(Q(responsible__user=user) | Q(created_by=user))
    if params.get("deadline") == "overdue":
        remarks = remarks.filter(status=Remark.Status.OPEN, due_date__lt=timezone.localdate())
    return remarks


class RemarkListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        remarks = filter_remarks(services.visible_remarks(project, request.user), request.query_params, request.user)
        return Response(RemarkSerializer(remarks.prefetch_related("comments", "photos"), many=True, context={"project": project}).data)

    def post(self, request, project_id):
        project = accessible_project(request.user, project_id)
        serializer = RemarkSerializer(data=request.data, context={"project": project})
        serializer.is_valid(raise_exception=True)
        remark = services.create_remark(project=project, actor=request.user, images=request.FILES.getlist("images"), **serializer.validated_data)
        return Response(RemarkSerializer(remark, context={"project": project}).data, status=status.HTTP_201_CREATED)


def accessible_remark(user, pk):
    remark = get_object_or_404(Remark.objects.filter(project__members__user=user, project__members__is_active=True).select_related("project"), pk=pk)
    services.require_remark_access(remark, user)
    return remark


class RemarkDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        remark = accessible_remark(request.user, pk)
        return Response(RemarkSerializer(remark, context={"project": remark.project}).data)

    def patch(self, request, pk):
        remark = accessible_remark(request.user, pk)
        serializer = RemarkSerializer(remark, data=request.data, partial=True, context={"project": remark.project})
        serializer.is_valid(raise_exception=True)
        data = {key: value for key, value in serializer.validated_data.items() if key in ("description", "responsible", "due_date")}
        remark = services.update_remark(remark=remark, actor=request.user, version=request.data.get("version"), **data)
        return Response(RemarkSerializer(remark, context={"project": remark.project}).data)


class RemarkActionView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request, pk):
        remark = accessible_remark(request.user, pk)
        serializer = RemarkActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        remark = services.remark_action(remark=remark, actor=request.user, images=request.FILES.getlist("images"), **serializer.validated_data)
        return Response(RemarkSerializer(remark, context={"project": remark.project}).data)


class StageAcceptanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get_stage(self, request, pk):
        return get_object_or_404(Stage.objects.filter(project__members__user=request.user, project__members__is_active=True).select_related("project"), pk=pk)

    def get(self, request, pk):
        stage = self.get_stage(request, pk)
        require_manager(stage.project, request.user)
        return Response({"stage": StageSerializer(stage).data, "blockers": services.acceptance_blockers(stage),
                         "decisions": StageDecisionSerializer(stage.decisions.all(), many=True).data})

    def post(self, request, pk):
        stage = self.get_stage(request, pk)
        serializer = AcceptanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action, reason = serializer.validated_data["action"], serializer.validated_data["reason"]
        if action == "submit":
            stage = services.submit_stage(stage=stage, actor=request.user)
        elif action == "accept":
            stage = services.accept_stage(stage=stage, actor=request.user)
        else:
            stage = services.return_stage(stage=stage, actor=request.user, reason=reason)
        return Response(StageSerializer(stage).data)


@login_required
def remark_photo_file(request, pk):
    """A remark photo is visible only to members who can see the remark; a direct URL gives strangers 404."""
    photo = get_object_or_404(RemarkPhoto.objects.select_related("remark__project"), pk=pk)
    try:
        services.require_remark_access(photo.remark, request.user)
    except APIException:
        raise Http404
    response = FileResponse(photo.file.open("rb"), content_type=photo.content_type)
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response
