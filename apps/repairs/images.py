"""Checks uploaded photos by their content, not by name or browser-sent type (ТЗ 4.16, 4.17)."""
import io

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

FORMATS = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}


def process_photo(upload):
    """Return a re-encoded copy without metadata (EXIF, GPS) plus its properties."""
    name = getattr(upload, "name", "файл")
    if upload.size > settings.PHOTO_MAX_BYTES:
        raise ValidationError({"images": f"«{name}» больше {settings.PHOTO_MAX_BYTES // (1024 * 1024)} МБ."})
    try:
        upload.seek(0)
        with Image.open(upload) as probe:
            image_format = probe.format
            if image_format not in FORMATS:
                raise ValidationError({"images": f"«{name}»: допустимы только JPEG, PNG и WebP."})
            if probe.width * probe.height > settings.PHOTO_MAX_PIXELS:
                raise ValidationError({"images": f"«{name}»: слишком большое разрешение."})
            probe.verify()
        upload.seek(0)
        with Image.open(upload) as source:
            image = ImageOps.exif_transpose(source)
            if image_format == "JPEG" and image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format=image_format, quality=88)
            width, height = image.size
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise ValidationError({"images": f"«{name}» не является корректным изображением."}) from error
    content_type, extension = FORMATS[image_format]
    data = buffer.getvalue()
    return ContentFile(data, name=f"photo.{extension}"), content_type, width, height, len(data)
