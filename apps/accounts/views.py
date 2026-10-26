from django.contrib.auth import views as auth_views
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import urlsafe_base64_decode
from django.views.generic import FormView
from django.db import transaction
from django.views.decorators.http import require_http_methods, require_POST

from .forms import LoginForm, RegisterForm, ResendVerificationForm, VerificationCodeForm
from .models import EmailVerificationCode, User
from .services import (VerificationError, confirm_verification_code, ensure_verification_mail_configured,
                       resend_delay, send_verification_email)
from .tokens import email_verification_token


class RegisterView(FormView):
    template_name = "accounts/register.html"
    form_class = RegisterForm
    success_url = reverse_lazy("accounts:check_email")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("accounts:profile")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            with transaction.atomic():
                user = form.save()
                send_verification_email(self.request, user)
        except VerificationError as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)
        self.request.session["pending_verification_email"] = user.email
        return super().form_valid(form)


@require_http_methods(["GET", "POST"])
def check_email(request):
    email = request.session.get("pending_verification_email")
    if not email:
        return redirect("accounts:resend_verification")
    form = VerificationCodeForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            confirm_verification_code(email, form.cleaned_data["code"])
        except VerificationError as error:
            form.add_error("code", str(error))
        else:
            request.session.pop("pending_verification_email", None)
            messages.success(request, "Email подтверждён. Войдите в аккаунт.")
            return redirect("accounts:login")
    return render(request, "accounts/check_email.html", {"form": form, "email": email,
        "resend_seconds": resend_delay(email)})


@require_POST
def resend_code(request):
    email = request.session.get("pending_verification_email")
    if not email:
        return redirect("accounts:resend_verification")
    user = User.objects.filter(email__iexact=email, is_active=True, is_email_verified=False).first()
    try:
        ensure_verification_mail_configured()
        if user:
            send_verification_email(request, user)
    except VerificationError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Если аккаунт ожидает подтверждения, новый код отправлен. Используйте последнее письмо.")
    return redirect("accounts:check_email")


def verify_email(request, uidb64, token):
    user = None
    try:
        user = User.objects.get(pk=urlsafe_base64_decode(uidb64).decode())
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        pass
    # Links issued before the migration remain valid until a new code is requested.
    ok = bool(user and not EmailVerificationCode.objects.filter(user=user).exists() and email_verification_token.check_token(user, token))
    if ok:
        user.is_email_verified = True
        user.save(update_fields=["is_email_verified"])
    return render(request, "accounts/verify_result.html", {"ok": ok}, status=200 if ok else 400)


class ResendVerificationView(FormView):
    template_name = "accounts/resend_verification.html"
    form_class = ResendVerificationForm
    success_url = reverse_lazy("accounts:check_email")

    def form_valid(self, form):
        # Ответ одинаковый для любого email — адреса нельзя перебирать.
        email = form.cleaned_data["email"].strip().lower()
        self.request.session["pending_verification_email"] = email
        user = User.objects.filter(email__iexact=email, is_active=True,
                                   is_email_verified=False).first()
        try:
            ensure_verification_mail_configured()
            if user:
                send_verification_email(self.request, user)
        except VerificationError as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)
        return super().form_valid(form)


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("accounts:login")  # выход только через POST (CSRF-защита)


@login_required
def profile(request):
    from apps.workspace.views import context
    return render(request, "accounts/profile.html", context(request, page="profile"))


# ---- Восстановление пароля (встроенные представления Django) ----
class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/emails/password_reset_email.txt"
    subject_template_name = "accounts/emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"
