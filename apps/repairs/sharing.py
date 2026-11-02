"""Публичные ссылки на фотоотчёт для отправки в мессенджеры.

Ссылка подписана SECRET_KEY и действует SHARE_MAX_AGE секунд: получатель видит описание и фото
одного отчёта без входа в систему, но не может перебором открыть другие отчёты или фото.
"""
from django.core import signing
from django.shortcuts import get_object_or_404
from django.http import Http404

from .models import PhotoReport

SHARE_SALT = "repairs.photo-report-share"
SHARE_MAX_AGE = 30 * 24 * 60 * 60  # 30 дней


def make_share_token(report):
    return signing.dumps({"r": report.pk}, salt=SHARE_SALT, compress=True)


def report_from_token(token):
    try:
        data = signing.loads(token, salt=SHARE_SALT, max_age=SHARE_MAX_AGE)
    except signing.BadSignature:  # включает SignatureExpired
        raise Http404
    return get_object_or_404(PhotoReport.objects.select_related("task", "author", "project").prefetch_related("photos"), pk=data.get("r"))
