from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import User
from .services import is_locked


class RegisterForm(forms.ModelForm):
    password1 = forms.CharField(label="Пароль", strip=False,
                                widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Повторите пароль", strip=False,
                                widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    class Meta:
        model = User
        fields = ("full_name", "email")
        widgets = {
            "full_name": forms.TextInput(attrs={"autocomplete": "name", "autofocus": True}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
        }

    def clean_full_name(self):
        name = self.cleaned_data["full_name"].strip()
        if len(name) < 2:
            raise ValidationError("Укажите имя")
        return name

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Пользователь с таким email уже зарегистрирован")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", "Пароли не совпадают")
            else:
                try:
                    validate_password(p1, User(email=cleaned.get("email", ""), full_name=cleaned.get("full_name", "")))
                except ValidationError as e:
                    self.add_error("password1", e)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class LoginForm(AuthenticationForm):
    username = forms.EmailField(label="Email",
                                widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}))
    password = forms.CharField(label="Пароль", strip=False,
                               widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    error_messages = {
        "invalid_login": "Неверный email или пароль",
        "inactive": "Аккаунт отключён",
        "unverified": "Email не подтверждён. Введите код из письма или запросите новый код.",
        "locked": "Слишком много неудачных попыток. Попробуйте позже.",
    }

    def clean(self):
        email = (self.cleaned_data.get("username") or "").strip().lower()
        if email and is_locked(email):
            raise ValidationError(self.error_messages["locked"], code="locked")
        return super().clean()

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_email_verified:
            raise ValidationError(self.error_messages["unverified"], code="unverified")


class ResendVerificationForm(forms.Form):
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}))


class VerifyEmailForm(forms.Form):
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    code = forms.RegexField(
        r"^\d{6}$",
        label="Код подтверждения",
        error_messages={"invalid": "Введите 6-значный код из письма."},
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric", "maxlength": "6"}),
    )


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}),
    )


class PasswordResetCodeForm(forms.Form):
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    code = forms.RegexField(
        r"^\d{6}$",
        label="Код из письма",
        error_messages={"invalid": "Введите 6-значный код из письма."},
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric", "maxlength": "6"}),
    )
    new_password1 = forms.CharField(
        label="Новый пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label="Повторите новый пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        password1 = cleaned.get("new_password1")
        password2 = cleaned.get("new_password2")
        if password1 and password2:
            if password1 != password2:
                self.add_error("new_password2", "Пароли не совпадают.")
            else:
                user = User.objects.filter(email__iexact=email.strip()).first() if email else None
                try:
                    validate_password(password1, user=user)
                except ValidationError as error:
                    self.add_error("new_password1", error)
        return cleaned
