from datetime import timedelta
import secrets

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils import timezone

from .models import EmailVerificationCode, LoginAttempt, PasswordResetCode


def _verification_code_hash(user, code: str) -> str:
    return salted_hmac(
        "apps.accounts.email_verification",
        f"{user.pk}:{code}",
        secret=settings.SECRET_KEY,
    ).hexdigest()


def send_verification_email(user) -> bool:
    verification = EmailVerificationCode.objects.filter(user=user).first()
    now = timezone.now()
    cooldown = timedelta(seconds=getattr(settings, "EMAIL_VERIFICATION_RESEND_COOLDOWN", 60))
    if verification and verification.created_at + cooldown > now:
        return False

    code = f"{secrets.randbelow(1_000_000):06d}"
    body = render_to_string(
        "accounts/emails/verify_email.txt",
        {"user": user, "code": code, "expiry_minutes": settings.EMAIL_VERIFICATION_CODE_TTL // 60},
    )
    subject = render_to_string("accounts/emails/verify_subject.txt").strip()
    send_mail(subject, body, None, [user.email])
    EmailVerificationCode.objects.update_or_create(
        user=user,
        defaults={"code_hash": _verification_code_hash(user, code), "attempts": 0},
    )
    return True


def verify_email_code(user, code: str) -> bool:
    try:
        verification = user.email_verification_code
    except EmailVerificationCode.DoesNotExist:
        return False

    if verification.attempts >= settings.EMAIL_VERIFICATION_MAX_ATTEMPTS:
        return False
    expiry = timedelta(seconds=settings.EMAIL_VERIFICATION_CODE_TTL)
    if verification.created_at + expiry <= timezone.now():
        return False

    if not constant_time_compare(verification.code_hash, _verification_code_hash(user, code)):
        verification.attempts += 1
        verification.save(update_fields=["attempts"])
        return False

    user.is_email_verified = True
    user.save(update_fields=["is_email_verified"])
    verification.delete()
    return True


def _password_reset_code_hash(user, code: str) -> str:
    return salted_hmac(
        "apps.accounts.password_reset",
        f"{user.pk}:{code}",
        secret=settings.SECRET_KEY,
    ).hexdigest()


def send_password_reset_code(user) -> bool:
    reset_code = PasswordResetCode.objects.filter(user=user).first()
    cooldown = timedelta(seconds=settings.PASSWORD_RESET_CODE_RESEND_COOLDOWN)
    if reset_code and reset_code.created_at + cooldown > timezone.now():
        return False

    code = f"{secrets.randbelow(1_000_000):06d}"
    body = render_to_string(
        "accounts/emails/password_reset_email.txt",
        {"user": user, "code": code, "expiry_minutes": settings.PASSWORD_RESET_CODE_TTL // 60},
    )
    subject = render_to_string("accounts/emails/password_reset_subject.txt").strip()
    send_mail(subject, body, None, [user.email])
    PasswordResetCode.objects.update_or_create(
        user=user,
        defaults={"code_hash": _password_reset_code_hash(user, code), "attempts": 0},
    )
    return True


@transaction.atomic
def reset_password_with_code(user, code: str, new_password: str) -> bool:
    reset_code = PasswordResetCode.objects.select_for_update().filter(user=user).first()
    if not reset_code or reset_code.attempts >= settings.PASSWORD_RESET_CODE_MAX_ATTEMPTS:
        return False

    expiry = timedelta(seconds=settings.PASSWORD_RESET_CODE_TTL)
    if reset_code.created_at + expiry <= timezone.now():
        return False

    if not constant_time_compare(reset_code.code_hash, _password_reset_code_hash(user, code)):
        reset_code.attempts += 1
        reset_code.save(update_fields=["attempts"])
        return False

    user.set_password(new_password)
    user.save(update_fields=["password"])
    reset_code.delete()
    return True


def client_ip(request):
    return request.META.get("REMOTE_ADDR") if request else None


def is_locked(email: str) -> bool:
    """True, если за последние LOGIN_LOCK_MINUTES было >= LOGIN_MAX_ATTEMPTS неудачных входов."""
    since = timezone.now() - timedelta(minutes=getattr(settings, "LOGIN_LOCK_MINUTES", 15))
    fails = LoginAttempt.objects.filter(email=email.strip().lower(), created_at__gt=since).count()
    return fails >= getattr(settings, "LOGIN_MAX_ATTEMPTS", 5)
