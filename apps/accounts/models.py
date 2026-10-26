from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
import uuid


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Укажите email")
        user = self.model(email=email.strip().lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_email_verified", True)
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Пользователь системы. Роль (заказчик / прораб / исполнитель) определяется
    НЕ здесь, а в модели «Участие в проекте» (ТЗ п. 3.2): в разных проектах роли разные."""

    email = models.EmailField("Email", unique=True)
    full_name = models.CharField("ФИО", max_length=150)
    is_active = models.BooleanField("Активен", default=True)
    is_staff = models.BooleanField("Доступ в админку", default=False)
    is_email_verified = models.BooleanField("Email подтверждён", default=False)
    date_joined = models.DateTimeField("Дата регистрации", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"

    def __str__(self):
        return self.email

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        parts = self.full_name.split()
        if len(parts) >= 2:
            return parts[1]  # ФИО: Фамилия Имя Отчество, берём имя
        return parts[0] if parts else self.email

class LoginAttempt(models.Model):
    """Неудачные попытки входа — для ограничения числа попыток (ТЗ п. 4.17)."""

    email = models.EmailField()
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["email", "created_at"])]
        verbose_name = "неудачная попытка входа"
        verbose_name_plural = "неудачные попытки входа"


class EmailVerificationCode(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="email_verification_code")
    code_hash = models.CharField(max_length=64, blank=True)
    nonce = models.UUIDField(default=uuid.uuid4, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    window_started_at = models.DateTimeField(default=timezone.now)
    send_count = models.PositiveSmallIntegerField(default=0)
