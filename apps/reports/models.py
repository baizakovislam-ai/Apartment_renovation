from django.conf import settings
from django.db import models


class PhotoReport(models.Model):
    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="photo_reports")
    task = models.ForeignKey("repairs.Task", on_delete=models.PROTECT, related_name="photo_reports")
    description = models.TextField()
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="photo_reports")
    request_id = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class ReportPhoto(models.Model):
    report = models.ForeignKey(PhotoReport, on_delete=models.PROTECT, related_name="photos")
    image = models.FileField(upload_to="private/reports/%Y/%m/")

    class Meta:
        ordering = ["id"]
