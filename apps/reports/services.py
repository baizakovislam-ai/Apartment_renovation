from io import BytesIO
from pathlib import Path
from uuid import uuid4
import warnings

from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

from apps.projects.services import lock_project, record_event
from apps.repairs.task_services import editable_stage, require_task_access
from .models import PhotoReport, ReportPhoto


def prepare_image(upload):
    if upload.size > 8 * 1024 * 1024:
        raise ValidationError("Каждая фотография должна быть не больше 8 МБ.")
    if Path(upload.name).suffix.lower() not in [".jpg", ".jpeg", ".png", ".webp"]:
        raise ValidationError("Разрешены JPEG, PNG и WebP.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(upload) as image:
                if image.format not in ["JPEG", "PNG", "WEBP"] or image.width * image.height > 25_000_000:
                    raise ValidationError("Недопустимый формат или размер изображения (до 25 Мп).")
                image.verify()
            upload.seek(0)
            with Image.open(upload) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((2560, 2560))
                output = BytesIO()
                if "A" in image.getbands():
                    image.convert("RGBA").save(output, "PNG")
                    extension = "png"
                else:
                    image.convert("RGB").save(output, "JPEG", quality=88)
                    extension = "jpg"
        return ContentFile(output.getvalue(), name=f"{uuid4().hex}.{extension}")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValidationError("Файл не является корректной фотографией.") from error


def create_report(*, task, actor, description, images, request_id=None):
    saved = []
    try:
        with transaction.atomic():
            project = lock_project(task.project)
            task.refresh_from_db()
            require_task_access(task, actor)
            request_id = request_id or uuid4()
            existing = PhotoReport.objects.filter(request_id=request_id).first()
            if existing:
                if existing.task_id == task.pk and existing.author_id == actor.pk:
                    return existing
                raise ValidationError("Идентификатор операции уже использован.")
            editable_stage(task.stage)
            if task.status == "done":
                raise ValidationError("Задача уже выполнена и проверена.")
            if not description.strip() or not 1 <= len(images) <= 8:
                raise ValidationError("Добавьте описание и от 1 до 8 фотографий.")
            prepared = [prepare_image(upload) for upload in images]
            report = PhotoReport.objects.create(project=project, task=task, author=actor, description=description.strip(), request_id=request_id)
            for content in prepared:
                photo = ReportPhoto(report=report)
                photo.image.save(content.name, content, save=False)
                saved.append((photo.image.storage, photo.image.name))
                photo.save()
            task.version += 1
            task.save(update_fields=["version", "updated_at"])
            record_event(project, actor, "report.created", task_id=task.pk, name=task.name, report_id=report.pk)
            return report
    except Exception:
        for storage, name in saved:
            storage.delete(name)
        raise
