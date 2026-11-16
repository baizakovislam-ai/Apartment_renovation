import logging
import smtplib

from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import FormView

from .forms import (
    LoginForm,
    PasswordResetCodeForm,
    PasswordResetNewPasswordForm,
    PasswordResetRequestForm,
    RegisterForm,
    ResendVerificationForm,
    VerifyEmailForm,
)
from .models import User
from .services import (
    send_password_reset_code,
    set_password_with_verified_code,
    send_verification_email,
    verify_email_code,
    verify_password_reset_code,
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


# ---- Восстановление пароля: email → код → новый пароль ----
RESET_EMAIL_KEY = "password_reset_email"
RESET_VERIFIED_KEY = "password_reset_verified"


def _reset_user(email):
    user = User.objects.filter(email__iexact=(email or "").strip(), is_active=True).first()
    return user if user and user.has_usable_password() else None


def _send_reset_code(user):
    if not user:
        return
    try:
        send_password_reset_code(user)
    except (smtplib.SMTPException, OSError):
        logger.exception("Password reset email delivery failed for user id %s", user.pk)


class PasswordResetView(FormView):
    """Шаг 1: email. Ответ одинаковый для любых адресов — так нельзя узнать, кто зарегистрирован."""
    template_name = "accounts/password_reset_form.html"
    form_class = PasswordResetRequestForm
    success_url = reverse_lazy("accounts:password_reset_confirm")

    def get_initial(self):
        return {**super().get_initial(), "email": self.request.session.get(RESET_EMAIL_KEY, "")}

    def form_valid(self, form):
        email = form.cleaned_data["email"].strip().lower()
        self.request.session[RESET_EMAIL_KEY] = email
        self.request.session.pop(RESET_VERIFIED_KEY, None)
        _send_reset_code(_reset_user(email))
        return super().form_valid(form)


class PasswordResetDoneView(View):
    """Старый адрес «письмо отправлено» — теперь сразу ведёт на ввод кода."""
    def get(self, request):
        return redirect("accounts:password_reset_confirm")


class PasswordResetConfirmView(FormView):
    """Шаг 2: только код из письма."""
    form_class = PasswordResetCodeForm
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_new")

    def dispatch(self, request, *args, **kwargs):
        self.email = request.session.get(RESET_EMAIL_KEY)
        if not self.email:
            return redirect("accounts:password_reset")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if "resend" in request.POST:
            _send_reset_code(_reset_user(self.email))
            messages.success(request, "Если адрес зарегистрирован, мы отправили новый код. Повторно запросить можно через минуту.")
            return redirect("accounts:password_reset_confirm")
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "email": self.email}

    def form_valid(self, form):
        user = _reset_user(self.email)
        code_id = verify_password_reset_code(user, form.cleaned_data["code"]) if user else None
        if not code_id:
            form.add_error("code", "Неверный или истёкший код. Проверьте письмо или запросите новый код.")
            return self.form_invalid(form)
        self.request.session[RESET_VERIFIED_KEY] = {"user": user.pk, "code": code_id}
        return super().form_valid(form)


class PasswordResetNewPasswordView(FormView):
    """Шаг 3: новый пароль — доступен только после верного кода."""
    form_class = PasswordResetNewPasswordForm
    template_name = "accounts/password_reset_new.html"
    success_url = reverse_lazy("accounts:password_reset_complete")

    def dispatch(self, request, *args, **kwargs):
        verified = request.session.get(RESET_VERIFIED_KEY) or {}
        self.user = User.objects.filter(pk=verified.get("user"), is_active=True).first()
        self.code_id = verified.get("code")
        if not self.user or not self.code_id:
            return redirect("accounts:password_reset_confirm" if request.session.get(RESET_EMAIL_KEY) else "accounts:password_reset")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.user}

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "email": self.user.email}

    def form_valid(self, form):
        if not set_password_with_verified_code(self.user, self.code_id, form.cleaned_data["new_password1"]):
            self.request.session.pop(RESET_VERIFIED_KEY, None)
            messages.error(self.request, "Время действия кода истекло. Запросите новый код.")
            return redirect("accounts:password_reset_confirm")
        self.request.session.pop(RESET_VERIFIED_KEY, None)
        self.request.session.pop(RESET_EMAIL_KEY, None)
        return super().form_valid(form)


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"
