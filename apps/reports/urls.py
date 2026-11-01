from django.urls import path
from . import views

urlpatterns = [
    path("projects/<int:project_id>/report/", views.ReportView.as_view(), name="project-report"),
    path("projects/<int:project_id>/report/pdf/", views.ReportPdfView.as_view(), name="project-report-pdf"),
]
