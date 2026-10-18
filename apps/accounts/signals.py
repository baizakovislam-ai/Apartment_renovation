from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver

from .models import LoginAttempt
from .services import client_ip


@receiver(user_login_failed)
def record_failed_login(sender, credentials, request=None, **kwargs):
    email = (credentials.get("username") or credentials.get("email") or "").strip().lower()
    if email:
        LoginAttempt.objects.create(email=email, ip=client_ip(request))


@receiver(user_logged_in)
def clear_failed_logins(sender, user, request=None, **kwargs):
    LoginAttempt.objects.filter(email=user.email.lower()).delete()
