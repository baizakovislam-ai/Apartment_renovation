"""Final project report page and its PDF (ТЗ FR21)."""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from apps.reports.pdf import render_pdf
from apps.reports.services import build_report
from .finance_views import finance_project
from .views import context


def pdf_response(report):
    response = HttpResponse(render_pdf(report), content_type="application/pdf")
    name = f"report-project-{report['project'].pk}-{report['generated_at']:%Y-%m-%d}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["Cache-Control"] = "no-store"
    return response


@login_required
def report_page(request, project_id):
    project = finance_project(request, project_id)
    return render(request, "workspace/report.html", context(request, project, "report", report=build_report(project, request.user)))


@login_required
def report_pdf(request, project_id):
    project = finance_project(request, project_id)
    return pdf_response(build_report(project, request.user))
