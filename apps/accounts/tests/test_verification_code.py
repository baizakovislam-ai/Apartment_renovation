from datetime import timedelta
from smtplib import SMTPAuthenticationError
from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.models import EmailVerificationCode, User
from apps.accounts.services import VerificationError, confirm_verification_code, send_verification_email
from apps.accounts.tokens import email_verification_token
from .test_auth import FAST_HASHERS, PASSWORD, mail_code


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class VerificationCodeTests(TestCase):
    def register(self):
        return self.client.post(reverse("accounts:register"), {"full_name": "Тестовый пользователь", "email": "tester@example.com", "password1": PASSWORD, "password2": PASSWORD})

    def code_post(self, code):
        return self.client.post(reverse("accounts:check_email"), {"code": code})

    def allow_resend(self):
        EmailVerificationCode.objects.update(sent_at=timezone.now() - timedelta(seconds=61))

    def test_registration_sends_six_digits_and_stores_only_hash(self):
        with patch("apps.accounts.services.secrets.randbelow", return_value=7):
            response = self.register()
        self.assertRedirects(response, reverse("accounts:check_email"))
        self.assertEqual(mail_code(), "000007")
        self.assertNotIn("http", mail.outbox[-1].body)
        record = EmailVerificationCode.objects.get()
        self.assertEqual(len(record.code_hash), 64)
        self.assertNotIn("000007", record.code_hash)
        self.assertEqual(record.send_count, 1)
        self.assertEqual(self.client.session["pending_verification_email"], "tester@example.com")
        self.assertNotContains(self.client.get(reverse("accounts:check_email")), "000007")
        self.assertRedirects(self.code_post("000007"), reverse("accounts:login"))
        record.refresh_from_db()
        self.assertEqual(record.code_hash, "")
        self.assertIsNotNone(record.consumed_at)
        self.assertTrue(User.objects.get().is_email_verified)

    def test_wrong_code_does_not_verify_or_reset_attempt_count(self):
        self.register()
        actual = mail_code()
        wrong = "999999" if actual != "999999" else "888888"
        self.assertContains(self.code_post(wrong), "Неверный код")
        self.assertEqual(EmailVerificationCode.objects.get().attempts, 1)
        self.assertFalse(User.objects.get().is_email_verified)
        self.assertRedirects(self.code_post(actual), reverse("accounts:login"))

    def test_attempt_limit_locks_correct_code_too(self):
        self.register()
        actual = mail_code()
        wrong = "999999" if actual != "999999" else "888888"
        for _ in range(5):
            self.code_post(wrong)
        self.assertEqual(EmailVerificationCode.objects.get().attempts, 5)
        self.assertContains(self.code_post(actual), "Слишком много неверных попыток")
        self.assertFalse(User.objects.get().is_email_verified)

    def test_expired_code_is_rejected(self):
        self.register()
        EmailVerificationCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertContains(self.code_post(mail_code()), "Срок действия кода истёк")
        self.assertFalse(User.objects.get().is_email_verified)

    def test_invalid_code_format_is_rejected(self):
        self.register()
        for value in ["12345", "1234567", "abcdef", "１２３４５６", "12 345"]:
            response = self.code_post(value)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(User.objects.get().is_email_verified)

    def test_resend_cooldown_is_enforced_on_server(self):
        self.register()
        response = self.client.post(reverse("accounts:resend_code"), follow=True)
        self.assertContains(response, "Повторный код можно запросить")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EmailVerificationCode.objects.get().send_count, 1)

    def test_resend_invalidates_previous_code_and_resets_attempts(self):
        self.register()
        old = mail_code()
        EmailVerificationCode.objects.update(attempts=5)
        self.allow_resend()
        response = self.client.post(reverse("accounts:resend_code"))
        self.assertRedirects(response, reverse("accounts:check_email"))
        new = mail_code()
        self.assertNotEqual(old, new)
        self.assertEqual(EmailVerificationCode.objects.get().attempts, 0)
        self.assertContains(self.code_post(old), "Неверный код")
        self.assertRedirects(self.code_post(new), reverse("accounts:login"))

    def test_hourly_send_limit_and_window_reset(self):
        self.register()
        EmailVerificationCode.objects.update(send_count=5)
        self.allow_resend()
        self.assertContains(self.client.post(reverse("accounts:resend_code"), follow=True), "Лимит отправки")
        self.assertEqual(len(mail.outbox), 1)
        EmailVerificationCode.objects.update(window_started_at=timezone.now() - timedelta(hours=1, seconds=1))
        self.client.post(reverse("accounts:resend_code"))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(EmailVerificationCode.objects.get().send_count, 1)

    def test_delivery_failure_does_not_leave_unusable_registration(self):
        with patch("apps.accounts.services.send_mail", side_effect=SMTPAuthenticationError(535, b"Authentication rejected")):
            response = self.register()
        self.assertContains(response, "Не удалось отправить код")
        self.assertFalse(User.objects.exists())
        self.assertFalse(EmailVerificationCode.objects.exists())
        self.assertNotIn("pending_verification_email", self.client.session)

    def test_resend_delivery_failure_preserves_old_code(self):
        self.register()
        code = mail_code()
        old_hash = EmailVerificationCode.objects.get().code_hash
        self.allow_resend()
        with patch("apps.accounts.services.send_mail", side_effect=OSError("Unavailable")):
            response = self.client.post(reverse("accounts:resend_code"), follow=True)
        self.assertContains(response, "Не удалось отправить код")
        self.assertEqual(EmailVerificationCode.objects.get().code_hash, old_hash)
        self.assertRedirects(self.code_post(code), reverse("accounts:login"))

    @override_settings(MAILERS={"default": {"BACKEND": "django.core.mail.backends.smtp.EmailBackend", "OPTIONS": {"host": "smtp.gmail.com", "port": 587, "username": "", "password": "", "use_tls": True}}})
    def test_missing_smtp_settings_show_honest_error(self):
        response = self.register()
        self.assertContains(response, "Отправка писем пока не настроена")
        self.assertFalse(User.objects.exists())

    def test_existing_unconfirmed_account_can_request_a_code(self):
        user = User.objects.create_user(email="tester@example.com", full_name="Существующий", password=PASSWORD)
        response = self.client.post(reverse("accounts:resend_verification"), {"email": "TESTER@example.com"})
        self.assertRedirects(response, reverse("accounts:check_email"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertRedirects(self.code_post(mail_code()), reverse("accounts:login"))
        user.refresh_from_db()
        self.assertTrue(user.is_email_verified)
        self.assertEqual(User.objects.count(), 1)

    def test_browsable_api_login_cannot_bypass_email_confirmation(self):
        self.register()
        self.client.post("/api/auth/login/", {"username": "tester@example.com", "password": PASSWORD})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.code_post(mail_code())
        response = self.client.post("/api/auth/login/", {"username": "tester@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_resend_for_unknown_and_verified_email_does_not_send(self):
        User.objects.create_user(email="verified@example.com", full_name="Подтверждённый", is_email_verified=True)
        for email in ["unknown@example.com", "verified@example.com"]:
            response = self.client.post(reverse("accounts:resend_verification"), {"email": email})
            self.assertRedirects(response, reverse("accounts:check_email"))
        self.assertEqual(len(mail.outbox), 0)

    def test_verification_requires_pending_session_and_post(self):
        self.assertRedirects(self.client.get(reverse("accounts:check_email")), reverse("accounts:resend_verification"))
        self.assertEqual(self.client.get(reverse("accounts:resend_code")).status_code, 405)
        self.register()
        code = mail_code()
        self.client.get(reverse("accounts:check_email") + "?code=" + code)
        self.assertFalse(User.objects.get().is_email_verified)
        other_browser = Client(enforce_csrf_checks=True)
        self.assertEqual(other_browser.post(reverse("accounts:check_email"), {"code": code}).status_code, 403)
        self.assertEqual(other_browser.post(reverse("accounts:resend_code")).status_code, 403)

    def test_old_link_remains_usable_only_until_a_new_code_is_requested(self):
        user = User.objects.create_user(email="old@example.com", full_name="Ранее зарегистрированный")
        path = reverse("accounts:verify_email", args=[urlsafe_base64_encode(force_bytes(user.pk)), email_verification_token.make_token(user)])
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 400)
        user.is_email_verified = False
        user.save()
        path = reverse("accounts:verify_email", args=[urlsafe_base64_encode(force_bytes(user.pk)), email_verification_token.make_token(user)])
        send_verification_email(None, user)
        self.assertEqual(self.client.get(path).status_code, 400)
