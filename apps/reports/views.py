from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.estimates.views import finance_project
from apps.workspace.report_views import pdf_response
from .services import build_report


class ReportView(APIView):
    """GET the final report figures of the project (customer and foreman)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        report = build_report(finance_project(request, project_id), request.user)
        approved, schedule = report["estimate"]["approved"], report["schedule"]
        return Response({
            "project": report["project"].pk, "generated_at": report["generated_at"], "progress": report["progress"]["percent"],
            "planned_start": schedule["planned_start"], "planned_end": schedule["planned_end"], "actual_end": schedule["actual_end"],
            "days_late": schedule["days_late"], "late_stages": schedule["late_stages"],
            "stages": [{"id": row["stage"].pk, "name": row["stage"].name, "status": row["stage"].status, "start_date": row["stage"].start_date,
                        "end_date": row["stage"].end_date, "accepted_at": row["accepted_at"], "days_late": row["days_late"],
                        "progress": row["progress"]} for row in report["stages"]],
            "estimate": {"id": approved.pk, "number": approved.number, "total": approved.total} if approved else None,
            "initial_estimate_total": report["estimate"]["initial_total"], "approved_changes": len(report["estimate"]["changes"]),
            "approved_changes_total": report["estimate"]["changes_total"], "expenses": len(report["expenses"]),
            "tasks_done": len(report["done_tasks"]), "photo_reports": report["photo_reports"],
            "remarks": len(report["remarks"]), "remarks_open": report["remarks_open"],
            "final_cost": report["final_cost"], "cost_source": report["cost_source"], "budget": report["budget"],
            "budget_left": report["budget_left"],
        })


class ReportPdfView(APIView):
    """GET the final report as a PDF file."""
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        return pdf_response(build_report(finance_project(request, project_id), request.user))
