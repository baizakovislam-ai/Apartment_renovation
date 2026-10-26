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
        "unverified": "Email не подтверждён. Введите шестизначный код из письма или запросите новый код.",
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


class VerificationCodeForm(forms.Form):
    code = forms.RegexField(
        label="Код из письма", regex=r"\A[0-9]{6}\Z", min_length=6, max_length=6,
        error_messages={"invalid": "Введите ровно 6 цифр.", "min_length": "Введите ровно 6 цифр.", "max_length": "Введите ровно 6 цифр."},
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "autofocus": True,
                                      "pattern": "[0-9]{6}", "maxlength": "6", "placeholder": "000000", "class": "verification-code"}),
    )
