from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import LoginAttempt
from .tokens import email_verification_token


def send_verification_email(request, user) -> None:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_verification_token.make_token(user)
    link = request.build_absolute_uri(reverse("accounts:verify_email", kwargs={"uidb64": uid, "token": token}))
    body = render_to_string("accounts/emails/verify_email.txt", {"user": user, "link": link})
    subject = render_to_string("accounts/emails/verify_subject.txt").strip()
    send_mail(subject, body, None, [user.email])


def client_ip(request):
    return request.META.get("REMOTE_ADDR") if request else None


def is_locked(email: str) -> bool:
    """True, если за последние LOGIN_LOCK_MINUTES было >= LOGIN_MAX_ATTEMPTS неудачных входов."""
    since = timezone.now() - timedelta(minutes=getattr(settings, "LOGIN_LOCK_MINUTES", 15))
    fails = LoginAttempt.objects.filter(email=email.strip().lower(), created_at__gt=since).count()
    return fails >= getattr(settings, "LOGIN_MAX_ATTEMPTS", 5)
