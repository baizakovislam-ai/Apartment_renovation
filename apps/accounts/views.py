import logging
import smtplib

from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import FormView

from .forms import (
    LoginForm,
    PasswordResetCodeForm,
    PasswordResetRequestForm,
    RegisterForm,
    ResendVerificationForm,
    VerifyEmailForm,
)
from .models import User
from .services import (
    reset_password_with_code,
    send_password_reset_code,
    send_verification_email,
    verify_email_code,
)

logger = logging.getLogger(__name__)


class RegisterView(FormView):
    template_name = "accounts/register.html"
    form_class = RegisterForm
    success_url = reverse_lazy("accounts:check_email")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("accounts:profile")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        self.request.session["pending_verification_email"] = user.email
        try:
            send_verification_email(user)
        except (smtplib.SMTPException, OSError):
            logger.exception("Verification email delivery failed for user id %s", user.pk)
            messages.error(
                self.request,
                "Аккаунт создан, но отправить код не удалось. Проверьте настройки почты "
                "и запросите код повторно.",
            )
        return super().form_valid(form)


def check_email(request):
    return VerifyEmailView.as_view(template_name="accounts/check_email.html")(request)


class VerifyEmailView(FormView):
    template_name = "accounts/verify_email.html"
    form_class = VerifyEmailForm
    success_url = reverse_lazy("accounts:login")

    def get_initial(self):
        return {**super().get_initial(), "email": self.request.session.get("pending_verification_email", "")}

    def form_valid(self, form):
        user = User.objects.filter(
            email__iexact=form.cleaned_data["email"].strip(),
            is_active=True,
            is_email_verified=False,
        ).first()
        if not user or not verify_email_code(user, form.cleaned_data["code"]):
            form.add_error("code", "Код недействителен или истёк. Запросите новый код.")
            return self.form_invalid(form)
        self.request.session.pop("pending_verification_email", None)
        return render(self.request, "accounts/verify_result.html", {"ok": True})


class ResendVerificationView(FormView):
    template_name = "accounts/resend_verification.html"
    form_class = ResendVerificationForm
    success_url = reverse_lazy("accounts:check_email")

    def form_valid(self, form):
        # Ответ одинаковый для любого email — адреса нельзя перебирать.
        self.request.session["pending_verification_email"] = form.cleaned_data["email"].strip().lower()
        user = User.objects.filter(email__iexact=form.cleaned_data["email"].strip(), is_active=True,
                                   is_email_verified=False).first()
        if user:
            try:
                sent = send_verification_email(user)
            except (smtplib.SMTPException, OSError):
                logger.exception("Verification email resend failed for user id %s", user.pk)
                messages.error(self.request, "Не удалось отправить код. Повторите отправку позже; если ошибка сохраняется, проверьте подключение Gmail.")
                return super().form_valid(form)
            if not sent:
                messages.info(self.request, "Код уже отправлен. Используйте последнее письмо или подождите минуту перед повторной отправкой.")
                return super().form_valid(form)
        messages.info(
            self.request,
            "Если этот адрес зарегистрирован и почта доступна, код будет отправлен. "
            "Если письмо не придёт, проверьте настройки почты.",
        )
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
class PasswordResetView(FormView):
    template_name = "accounts/password_reset_form.html"
    form_class = PasswordResetRequestForm
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form):
        user = User.objects.filter(
            email__iexact=form.cleaned_data["email"].strip(),
            is_active=True,
        ).first()
        if user and user.has_usable_password():
            try:
                send_password_reset_code(user)
            except (smtplib.SMTPException, OSError):
                logger.exception("Password reset email delivery failed for user id %s", user.pk)
        return super().form_valid(form)


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(FormView):
    form_class = PasswordResetCodeForm
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        user = User.objects.filter(
            email__iexact=form.cleaned_data["email"].strip(),
            is_active=True,
        ).first()
        if (
            not user
            or not user.has_usable_password()
            or not reset_password_with_code(
                user,
                form.cleaned_data["code"],
                form.cleaned_data["new_password1"],
            )
        ):
            form.add_error("code", "Код недействителен или истёк. Запросите новый код.")
            return self.form_invalid(form)
        return super().form_valid(form)


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"
