from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import urlsafe_base64_decode
from django.views.generic import FormView

from .forms import LoginForm, RegisterForm, ResendVerificationForm
from .models import User
from .services import send_verification_email
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
        user = form.save()
        send_verification_email(self.request, user)
        return super().form_valid(form)


def check_email(request):
    return render(request, "accounts/check_email.html")


def verify_email(request, uidb64, token):
    user = None
    try:
        user = User.objects.get(pk=urlsafe_base64_decode(uidb64).decode())
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        pass
    ok = bool(user and email_verification_token.check_token(user, token))
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
        user = User.objects.filter(email__iexact=form.cleaned_data["email"].strip(), is_active=True,
                                   is_email_verified=False).first()
        if user:
            send_verification_email(self.request, user)
        return super().form_valid(form)


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("accounts:login")  # выход только через POST (CSRF-защита)


@login_required
def profile(request):
    return render(request, "accounts/profile.html")


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
