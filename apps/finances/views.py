"""API of expenses (FR12) and the financial summary (FR13). The executor gets 403, strangers 404."""
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.projects.views import accessible_project
from . import services
from .models import Expense
from .serializers import ExpenseActionSerializer, ExpenseSerializer

EDIT_FIELDS = ("stage", "amount", "date", "recipient", "purpose", "category", "note")


def truthy(value):
    return str(value).lower() in ("1", "true", "yes", "on")


class ExpenseListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        expenses = services.filter_expenses(services.visible_expenses(project, request.user), request.query_params)
        return Response(ExpenseSerializer(expenses.select_related("stage"), many=True, context={"project": project}).data)

    def post(self, request, project_id):
        project = accessible_project(request.user, project_id)
        services.require_finance_access(project, request.user)
        serializer = ExpenseSerializer(data=request.data, context={"project": project})
        serializer.is_valid(raise_exception=True)
        data = {key: value for key, value in serializer.validated_data.items() if key in EDIT_FIELDS}
        expense = services.create_expense(project=project, actor=request.user, submit=truthy(request.data.get("submit", "")),
                                          document=request.FILES.get("document"),
                                          request_id=serializer.validated_data.get("request_id"), **data)
        return Response(ExpenseSerializer(expense, context={"project": project}).data, status=status.HTTP_201_CREATED)


def accessible_expense(user, pk):
    expense = get_object_or_404(Expense.objects.filter(project__members__user=user, project__members__is_active=True)
                                .select_related("project").distinct(), pk=pk)
    if not services.visible_expenses(expense.project, user).filter(pk=expense.pk).exists():
        raise Http404
    return expense


class ExpenseDetailView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request, pk):
        expense = accessible_expense(request.user, pk)
        return Response(ExpenseSerializer(expense, context={"project": expense.project}).data)

    def patch(self, request, pk):
        expense = accessible_expense(request.user, pk)
        serializer = ExpenseSerializer(expense, data=request.data, partial=True, context={"project": expense.project})
        serializer.is_valid(raise_exception=True)
        data = {key: value for key, value in serializer.validated_data.items() if key in EDIT_FIELDS}
        expense = services.update_expense(expense=expense, actor=request.user, revision=request.data.get("revision"),
                                          document=request.FILES.get("document"),
                                          remove_document=truthy(request.data.get("remove_document", "")), **data)
        return Response(ExpenseSerializer(expense, context={"project": expense.project}).data)

    def delete(self, request, pk):
        expense = accessible_expense(request.user, pk)
        services.delete_expense(expense=expense, actor=request.user, revision=request.data.get("revision"))
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExpenseActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        expense = accessible_expense(request.user, pk)
        serializer = ExpenseActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        expense = services.expense_action(expense=expense, actor=request.user, **serializer.validated_data)
        return Response(ExpenseSerializer(expense, context={"project": expense.project}).data)


class FinanceSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = accessible_project(request.user, project_id)
        services.require_finance_access(project, request.user)
        summary = services.finance_summary(project)
        text = lambda value: None if value is None else f"{value:.2f}"
        rows = lambda items: [{"label": item["label"], "planned": text(item["planned"]), "actual": text(item["actual"]),
                               "delta": text(item["delta"]), "used_percent": item["used"]} for item in items]
        return Response({
            "budget": text(summary["budget"]), "estimate_total": text(summary["estimate_total"]),
            "estimate_version": summary["estimate"].number if summary["estimate"] else None,
            "spent": text(summary["spent"]), "pending_total": text(summary["pending_total"]),
            "remaining": text(summary["remaining"]), "over_budget": summary["over_budget"], "budget_used_percent": summary["budget_used"],
            "deviation": text(summary["deviation"]), "deviation_percent": summary["deviation_percent"],
            "by_category": rows(summary["by_category"]), "by_stage": rows(summary["by_stage"]), "warnings": summary["warnings"],
        })


@login_required
def expense_document(request, pk):
    """A supporting document opens only for the customer and the foreman of that project; anybody else gets 404."""
    expense = get_object_or_404(Expense.objects.select_related("project"), pk=pk)
    try:
        visible = services.visible_expenses(expense.project, request.user).filter(pk=expense.pk).exists()
    except APIException:
        visible = False
    if not visible or not expense.document:
        raise Http404
    response = FileResponse(expense.document.open("rb"), content_type=expense.document_type or "application/octet-stream")
    response["Content-Disposition"] = "inline" if expense.document_type != "application/pdf" else 'inline; filename="document.pdf"'
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response
