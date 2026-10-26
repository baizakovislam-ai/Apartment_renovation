from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.projects.permissions import require_manager
from apps.projects.views import accessible_project
from apps.repairs.models import Stage
from apps.repairs.serializers import StageSerializer
from . import services
from .models import Remark
from .serializers import AcceptanceSerializer, RemarkActionSerializer, RemarkSerializer, StageDecisionSerializer


def filter_remarks(remarks, params):
    if params.get("status") in Remark.Status.values:
        remarks = remarks.filter(status=params["status"])
    if str(params.get("stage") or "").isdigit():
        remarks = remarks.filter(stage_id=params["stage"])
    query = (params.get("q") or "").strip()
    if query:
        remarks = remarks.filter(description__icontains=query)
    return remarks


class RemarkListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        remarks = filter_remarks(services.visible_remarks(project, request.user), request.query_params).prefetch_related("comments")
        return Response(RemarkSerializer(remarks, many=True, context={"project": project}).data)

    def post(self, request, project_id):
        project = accessible_project(request.user, project_id)
        serializer = RemarkSerializer(data=request.data, context={"project": project})
        serializer.is_valid(raise_exception=True)
        remark = services.create_remark(project=project, actor=request.user, **serializer.validated_data)
        return Response(RemarkSerializer(remark, context={"project": project}).data, status=status.HTTP_201_CREATED)


class RemarkActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        remark = get_object_or_404(Remark.objects.filter(project__members__user=request.user).select_related("project"), pk=pk)
        serializer = RemarkActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        remark = services.remark_action(remark=remark, actor=request.user, **serializer.validated_data)
        return Response(RemarkSerializer(remark, context={"project": remark.project}).data)


class StageAcceptanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get_stage(self, request, pk):
        return get_object_or_404(Stage.objects.filter(project__members__user=request.user).select_related("project"), pk=pk)

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
