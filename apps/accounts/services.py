from datetime import timedelta
import logging
import secrets
from smtplib import SMTPException
from uuid import uuid4

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import EmailVerificationCode, LoginAttempt, User

logger = logging.getLogger(__name__)


class VerificationError(Exception):
    pass


class VerificationDeliveryError(VerificationError):
    pass


def ensure_verification_mail_configured():
    mailer = settings.MAILERS.get("default", {})
    if mailer.get("BACKEND") == "django.core.mail.backends.smtp.EmailBackend":
        options = mailer.get("OPTIONS", {})
        if not options.get("username") or not options.get("password"):
            raise VerificationDeliveryError("Отправка писем пока не настроена. Заполните адрес Gmail и пароль приложения в .env, затем перезапустите сервер.")


def code_digest(user, nonce, code):
    return salted_hmac("accounts.email-code", f"{user.pk}:{user.email}:{nonce}:{code}", algorithm="sha256").hexdigest()


def send_verification_email(request, user) -> None:
    ensure_verification_mail_configured()
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        if user.is_email_verified or not user.is_active:
            return
        record, _ = EmailVerificationCode.objects.get_or_create(user=user)
        now = timezone.now()
        cooldown = settings.EMAIL_VERIFICATION_RESEND_SECONDS
        if record.sent_at and now < record.sent_at + timedelta(seconds=cooldown):
            remaining = max(1, int((record.sent_at + timedelta(seconds=cooldown) - now).total_seconds()) + 1)
            raise VerificationError(f"Повторный код можно запросить через {remaining} сек.")
        if now >= record.window_started_at + timedelta(hours=1):
            record.window_started_at, record.send_count = now, 0
        if record.send_count >= settings.EMAIL_VERIFICATION_MAX_SENDS_PER_HOUR:
            raise VerificationError("Лимит отправки кодов исчерпан. Попробуйте через час.")
        code = f"{secrets.randbelow(1_000_000):06d}"
        while record.code_hash and constant_time_compare(record.code_hash, code_digest(user, record.nonce, code)):
            code = f"{secrets.randbelow(1_000_000):06d}"
        record.nonce = uuid4()
        record.code_hash = code_digest(user, record.nonce, code)
        record.sent_at = now
        record.expires_at = now + timedelta(seconds=settings.EMAIL_VERIFICATION_TTL_SECONDS)
        record.attempts = 0
        record.consumed_at = None
        record.send_count += 1
        record.save()
        body = render_to_string("accounts/emails/verify_email.txt", {"user": user, "code": code, "minutes": settings.EMAIL_VERIFICATION_TTL_SECONDS // 60})
        subject = render_to_string("accounts/emails/verify_subject.txt").strip()
        try:
            sent = send_mail(subject, body, None, [user.email])
            if sent != 1:
                raise VerificationDeliveryError("Почтовый сервер не принял письмо. Попробуйте позже.")
        except (SMTPException, OSError) as error:
            logger.warning("Verification email delivery failed (%s)", type(error).__name__)
            raise VerificationDeliveryError("Не удалось отправить код. Проверьте настройки Gmail и соединение с почтовым сервером, затем повторите отправку.") from error


def confirm_verification_code(email, code):
    error = None
    with transaction.atomic():
        user = User.objects.select_for_update().filter(email__iexact=email, is_active=True).first()
        record = EmailVerificationCode.objects.filter(user=user).first() if user else None
        now = timezone.now()
        if not user or user.is_email_verified or not record or record.consumed_at or not record.code_hash:
            error = "Код недействителен или уже использован. Запросите новый код."
        elif not record.expires_at or record.expires_at <= now:
            error = "Срок действия кода истёк. Запросите новый код."
        elif record.attempts >= settings.EMAIL_VERIFICATION_MAX_ATTEMPTS:
            error = "Слишком много неверных попыток. Запросите новый код."
        elif not constant_time_compare(record.code_hash, code_digest(user, record.nonce, code)):
            record.attempts += 1
            record.save(update_fields=["attempts"])
            remaining = settings.EMAIL_VERIFICATION_MAX_ATTEMPTS - record.attempts
            error = f"Неверный код. Осталось попыток: {remaining}." if remaining else "Слишком много неверных попыток. Запросите новый код."
        else:
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified"])
            record.code_hash, record.consumed_at = "", now
            record.save(update_fields=["code_hash", "consumed_at"])
    # Raise outside atomic: failed-attempt counters must survive the error.
    if error:
        raise VerificationError(error)
    return user


def resend_delay(email):
    record = EmailVerificationCode.objects.filter(user__email__iexact=email).first()
    if not record or not record.sent_at:
        return 0
    return max(0, int((record.sent_at + timedelta(seconds=settings.EMAIL_VERIFICATION_RESEND_SECONDS) - timezone.now()).total_seconds()) + 1)


def client_ip(request):
    return request.META.get("REMOTE_ADDR") if request else None


def is_locked(email: str) -> bool:
    """True, если за последние LOGIN_LOCK_MINUTES было >= LOGIN_MAX_ATTEMPTS неудачных входов."""
    since = timezone.now() - timedelta(minutes=getattr(settings, "LOGIN_LOCK_MINUTES", 15))
    fails = LoginAttempt.objects.filter(email=email.strip().lower(), created_at__gt=since).count()
    return fails >= getattr(settings, "LOGIN_MAX_ATTEMPTS", 5)
