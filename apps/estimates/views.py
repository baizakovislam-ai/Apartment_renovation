from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.projects.serializers import ProjectSerializer
from apps.projects.views import accessible_project
from apps.repairs.models import Stage
from . import change_services, search, services
from .models import Calculation, CalculationItem, ChangeItem, ChangeRequest, Estimate, EstimateLine
from .serializers import (CalculationItemSerializer, CalculationSerializer, ChangeActionSerializer, ChangeItemWriteSerializer,
                          ChangeRequestSerializer, ChangeRequestWriteSerializer, EstimateActionSerializer, EstimateLineSerializer,
                          EstimateSerializer)

LINE_KEYS = ("stage", "name", "cost_type", "unit", "quantity", "price")
ITEM_KEYS = ("work", "cost_type", "unit", "volume", "price")


def finance_project(request, project_id):
    project = accessible_project(request.user, project_id)
    services.require_finance_access(project, request.user)
    return project


def finance_object(request, model, pk, path):
    obj = get_object_or_404(model.objects.filter(**{f"{path}__members__user": request.user, f"{path}__members__is_active": True}), pk=pk)
    project = obj
    for part in path.split("__"):
        project = getattr(project, part)
    services.require_finance_access(project, request.user)
    return obj, project


class EstimateListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = finance_project(request, project_id)
        return Response(EstimateSerializer(project.estimates.prefetch_related("lines__stage"), many=True).data)

    def post(self, request, project_id):
        estimate = services.create_draft(project=finance_project(request, project_id), actor=request.user)
        return Response(EstimateSerializer(estimate).data, status=status.HTTP_201_CREATED)


class EstimateDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        estimate, project = finance_object(request, Estimate, pk, "project")
        return Response(EstimateSerializer(estimate).data)


class EstimateActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        estimate, project = finance_object(request, Estimate, pk, "project")
        serializer = EstimateActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action, data = serializer.validated_data["action"], serializer.validated_data
        if action == "submit":
            estimate = services.submit_estimate(estimate=estimate, actor=request.user, comment=data["comment"])
        elif action == "withdraw":
            estimate = services.withdraw_estimate(estimate=estimate, actor=request.user)
        elif action == "approve":
            estimate = services.approve_estimate(estimate=estimate, actor=request.user)
        else:
            estimate = services.reject_estimate(estimate=estimate, actor=request.user, reason=data["reason"])
        return Response(EstimateSerializer(estimate).data)


class EstimateLineCreateView(APIView):
    """POST a line; body needs the estimate's current `revision` (protection from stale edits)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        estimate, project = finance_object(request, Estimate, pk, "project")
        serializer = EstimateLineSerializer(data=request.data, context={"project": project})
        serializer.is_valid(raise_exception=True)
        data = {key: value for key, value in serializer.validated_data.items() if key in LINE_KEYS}
        line = services.add_line(estimate=estimate, actor=request.user, revision=request.data.get("revision"), **data)
        return Response(EstimateLineSerializer(line).data, status=status.HTTP_201_CREATED)


class EstimateLineDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        line, project = finance_object(request, EstimateLine, pk, "estimate__project")
        serializer = EstimateLineSerializer(line, data=request.data, partial=True, context={"project": project})
        serializer.is_valid(raise_exception=True)
        data = {key: value for key, value in serializer.validated_data.items() if key in LINE_KEYS}
        line = services.update_line(line=line, actor=request.user, revision=request.data.get("revision"), **data)
        return Response(EstimateLineSerializer(line).data)

    def delete(self, request, pk):
        line, project = finance_object(request, EstimateLine, pk, "estimate__project")
        services.delete_line(line=line, actor=request.user, revision=request.data.get("revision") or request.query_params.get("revision"))
        return Response(status=status.HTTP_204_NO_CONTENT)


class StartWorksView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, project_id):
        project = services.start_works(project=accessible_project(request.user, project_id), actor=request.user)
        return Response(ProjectSerializer(project).data)


class CalculationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = finance_project(request, project_id)
        return Response(CalculationSerializer(project.calculations.prefetch_related("items"), many=True).data)

    def post(self, request, project_id):
        project = finance_project(request, project_id)
        calculation = services.create_calculation(project=project, actor=request.user, name=request.data.get("name", ""), note=request.data.get("note", ""))
        return Response(CalculationSerializer(calculation).data, status=status.HTTP_201_CREATED)


class CalculationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        calculation, project = finance_object(request, Calculation, pk, "project")
        return Response(CalculationSerializer(calculation).data)

    def patch(self, request, pk):
        calculation, project = finance_object(request, Calculation, pk, "project")
        calculation = services.update_calculation(calculation=calculation, actor=request.user,
                                                  name=request.data.get("name", calculation.name), note=request.data.get("note", calculation.note))
        return Response(CalculationSerializer(calculation).data)

    def delete(self, request, pk):
        calculation, project = finance_object(request, Calculation, pk, "project")
        services.delete_calculation(calculation=calculation, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CalculationItemCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        calculation, project = finance_object(request, Calculation, pk, "project")
        serializer = CalculationItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = {key: value for key, value in serializer.validated_data.items() if key in ITEM_KEYS}
        item = services.add_item(calculation=calculation, actor=request.user, **data)
        return Response(CalculationItemSerializer(item).data, status=status.HTTP_201_CREATED)


class CalculationItemDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        item, project = finance_object(request, CalculationItem, pk, "calculation__project")
        serializer = CalculationItemSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = {key: value for key, value in serializer.validated_data.items() if key in ITEM_KEYS}
        item = services.update_item(item=item, actor=request.user, **data)
        return Response(CalculationItemSerializer(item).data)

    def delete(self, request, pk):
        item, project = finance_object(request, CalculationItem, pk, "calculation__project")
        services.delete_item(item=item, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CalculationToEstimateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        calculation, project = finance_object(request, Calculation, pk, "project")
        stage = Stage.objects.filter(project=project, pk=request.data.get("stage") or 0).first()
        estimate = services.calculation_to_estimate(calculation=calculation, actor=request.user, stage=stage)
        return Response(EstimateSerializer(estimate).data)


class ApprovalListView(APIView):
    """Approvals journal (FR17): ?q=&type=estimate|acceptance&status=pending|approved|rejected|other&author=&date_from=&date_to="""
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = finance_project(request, project_id)
        items = search.approval_items(project, request.user, request.query_params)
        return Response([{
            "type": item["type"], "title": item["title"], "object_id": item["object_id"], "status": item["status"],
            "status_label": item["status_label"], "amount": str(item["amount"]) if item["amount"] is not None else None,
            "author": item["author"].pk, "created_at": item["created_at"], "decided_by": item["decided_by"].pk if item["decided_by"] else None,
            "decided_at": item["decided_at"], "text": item["text"],
        } for item in items])


class ProjectSearchView(APIView):
    """Project-wide search: ?q= (at least 2 characters). Results follow the role's visibility."""
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        results = search.project_search(project, request.user, request.query_params.get("q", ""))
        if results is None:
            return Response({"detail": "Введите хотя бы 2 символа."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "count": results["count"],
            "tasks": [{"id": task.pk, "name": task.name, "status": task.status} for task in results["tasks"]],
            "stages": [{"id": stage.pk, "name": stage.name, "status": stage.status} for stage in results["stages"]],
            "remarks": [{"id": remark.pk, "description": remark.description, "status": remark.status} for remark in results["remarks"]],
            "members": [{"id": member.pk, "name": member.user.get_full_name(), "role": member.role} for member in results["members"]],
            "estimate_lines": [{"id": line.pk, "name": line.name, "estimate": line.estimate.number, "amount": str(line.amount)} for line in results["estimate_lines"]],
            "calculations": [{"id": calc.pk, "name": calc.name} for calc in results["calculations"]],
        })


# ---------- changes of the approved estimate (FR11) ----------

class ChangeRequestListCreateView(APIView):
    """GET the project's change requests; POST {reason, schedule_stage?, new_end_date?} creates a draft."""
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = finance_project(request, project_id)
        changes = project.change_requests.select_related("estimate").prefetch_related("items__line", "items__stage")
        return Response(ChangeRequestSerializer(changes, many=True).data)

    def post(self, request, project_id):
        project = finance_project(request, project_id)
        serializer = ChangeRequestWriteSerializer(data=request.data, project=project)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        change = change_services.create_request(project=project, actor=request.user, reason=data["reason"],
                                                schedule_stage=data["schedule_stage"], new_end_date=data["new_end_date"])
        return Response(ChangeRequestSerializer(change).data, status=status.HTTP_201_CREATED)


class ChangeRequestDetailView(APIView):
    """GET one request; PATCH {reason, schedule_stage, new_end_date, revision} edits a draft; DELETE removes an own draft."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        change, project = finance_object(request, ChangeRequest, pk, "project")
        return Response(ChangeRequestSerializer(change).data)

    def patch(self, request, pk):
        change, project = finance_object(request, ChangeRequest, pk, "project")
        serializer = ChangeRequestWriteSerializer(data=request.data, project=project)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        change = change_services.update_request(request=change, actor=request.user, revision=data.get("revision"), reason=data["reason"],
                                                schedule_stage=data["schedule_stage"], new_end_date=data["new_end_date"])
        return Response(ChangeRequestSerializer(change).data)

    def delete(self, request, pk):
        change, project = finance_object(request, ChangeRequest, pk, "project")
        change_services.delete_request(request=change, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChangeItemCreateView(APIView):
    """POST {action: add|update|remove, line?, stage, name, cost_type, unit, quantity, price, revision}."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        change, project = finance_object(request, ChangeRequest, pk, "project")
        serializer = ChangeItemWriteSerializer(data=request.data, change=change)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        change_services.set_item(request=change, actor=request.user, revision=data.pop("revision"), action=data.pop("action"),
                                 line=data.pop("line"), **data)
        change.refresh_from_db()
        return Response(ChangeRequestSerializer(change).data, status=status.HTTP_201_CREATED)


class ChangeItemDetailView(APIView):
    """DELETE ?revision=N removes a changed position from a draft."""
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        item, project = finance_object(request, ChangeItem, pk, "request__project")
        revision = request.query_params.get("revision") or request.data.get("revision")
        change_services.delete_item(item=item, actor=request.user, revision=revision)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChangeActionView(APIView):
    """POST {action: submit|withdraw|approve|reject, reason?, revision?}."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        change, project = finance_object(request, ChangeRequest, pk, "project")
        serializer = ChangeActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action, data = serializer.validated_data["action"], serializer.validated_data
        if action == "submit":
            change = change_services.submit_request(request=change, actor=request.user, revision=data.get("revision"))
        elif action == "withdraw":
            change = change_services.withdraw_request(request=change, actor=request.user)
        elif action == "approve":
            change = change_services.approve_request(request=change, actor=request.user)
        else:
            change = change_services.reject_request(request=change, actor=request.user, reason=data["reason"])
        return Response(ChangeRequestSerializer(change).data)
