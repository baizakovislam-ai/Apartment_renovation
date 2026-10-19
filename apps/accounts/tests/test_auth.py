import re
import smtplib
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import EmailVerificationCode, LoginAttempt, User

PASSWORD = "Strong-Pass-1957"
FAST_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def verification_code():
    """Код из последнего письма."""
    return re.search(r"\b\d{6}\b", mail.outbox[-1].body).group(0)


@override_settings(PASSWORD_HASHERS=FAST_HASHERS, LOGIN_MAX_ATTEMPTS=3)
class AuthFlowTests(TestCase):
    def register(self, email="Ivan@Example.com", password=PASSWORD):
        return self.client.post(reverse("accounts:register"), {
            "full_name": "Иван Петров", "email": email, "password1": password, "password2": password})

    def verify(self):
        return self.client.post(reverse("accounts:verify_email"), {
            "email": "ivan@example.com", "code": verification_code()})

    def login(self, password=PASSWORD):
        return self.client.post(reverse("accounts:login"), {"username": "ivan@example.com", "password": password})

    def test_register_sends_email_and_blocks_login_until_verified(self):
        r = self.register()
        self.assertRedirects(r, reverse("accounts:check_email"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(User.objects.get().is_email_verified)
        r = self.login()
        self.assertEqual(r.status_code, 200)  # осталась на форме с ошибкой
        self.assertNotIn("_auth_user_id", self.client.session)

    @patch(
        "apps.accounts.views.send_verification_email",
        side_effect=smtplib.SMTPAuthenticationError(535, b"authentication failed"),
    )
    def test_registration_survives_smtp_failure_and_shows_retry_message(self, _send_email):
        with self.assertLogs("apps.accounts.views", level="ERROR"):
            response = self.register()
        self.assertRedirects(response, reverse("accounts:check_email"), fetch_redirect_response=False)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(EmailVerificationCode.objects.count(), 0)
        self.assertFalse(User.objects.get().is_email_verified)
        response = self.client.get(reverse("accounts:check_email"))
        self.assertContains(response, "Аккаунт создан, но отправить код не удалось")

    @patch(
        "apps.accounts.views.send_verification_email",
        side_effect=smtplib.SMTPAuthenticationError(535, b"authentication failed"),
    )
    def test_resending_after_smtp_failure_does_not_return_server_error(self, _send_email):
        User.objects.create_user(email="ivan@example.com", full_name="Иван Петров", password=PASSWORD)
        with self.assertLogs("apps.accounts.views", level="ERROR"):
            response = self.client.post(reverse("accounts:resend_verification"), {
                "email": "ivan@example.com"})
        self.assertRedirects(response, reverse("accounts:check_email"), fetch_redirect_response=False)
        self.assertEqual(response.status_code, 302)

    def test_verify_then_login_and_profile(self):
        self.register()
        self.verify()
        r = self.login()
        self.assertRedirects(r, reverse("accounts:profile"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)

    def test_verification_code_is_single_use(self):
        self.register()
        self.assertEqual(self.verify().status_code, 200)
        code = verification_code()
        code = "000000" if code != "000000" else "000001"
        r = self.client.post(reverse("accounts:verify_email"), {"email": "ivan@example.com", "code": code})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Код недействителен")

    @override_settings(EMAIL_VERIFICATION_RESEND_COOLDOWN=0)
    def test_resending_sends_a_code_that_can_be_used(self):
        self.register()
        response = self.client.post(reverse("accounts:resend_verification"), {
            "email": "ivan@example.com"})
        self.assertRedirects(response, reverse("accounts:check_email"))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(self.verify().status_code, 200)

    def test_invalid_code_is_rejected_and_attempts_are_limited(self):
        self.register()
        invalid_code = "000000" if verification_code() != "000000" else "000001"
        for _ in range(5):
            response = self.client.post(reverse("accounts:verify_email"), {
                "email": "ivan@example.com", "code": invalid_code})
        self.assertContains(response, "Код недействителен")
        self.assertFalse(User.objects.get().is_email_verified)
        self.assertEqual(self.verify().status_code, 200)
        self.assertFalse(User.objects.get().is_email_verified)

    @override_settings(EMAIL_VERIFICATION_CODE_TTL=0)
    def test_expired_code_is_rejected(self):
        self.register()
        response = self.verify()
        self.assertContains(response, "Код недействителен")
        self.assertFalse(User.objects.get().is_email_verified)

    def test_duplicate_email_case_insensitive(self):
        self.register()
        r = self.register(email="IVAN@example.com")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_weak_password_rejected(self):
        r = self.register(password="12345678")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(User.objects.count(), 0)

    def test_profile_requires_login(self):
        r = self.client.get(reverse("accounts:profile"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts:login"), r.url)

    def test_logout_requires_post_and_ends_session(self):
        self.register(); self.verify(); self.login()
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_lockout_after_max_attempts(self):
        self.register(); self.verify()
        for _ in range(3):
            self.login("Wrong-Pass-1")
        self.assertEqual(LoginAttempt.objects.count(), 3)
        r = self.login(PASSWORD)  # даже верный пароль не пускает
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(r, "Слишком много неудачных попыток")

    def test_password_reset_flow(self):
        self.register(); self.verify()
        mail.outbox.clear()
        # несуществующий email — тот же ответ, письмо не уходит
        r = self.client.post(reverse("accounts:password_reset"), {"email": "nobody@example.com"})
        self.assertRedirects(r, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

        self.client.post(reverse("accounts:password_reset"), {"email": "ivan@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIsNone(re.search(r"https?://\S+", mail.outbox[-1].body))
        reset_code = re.search(r"\b\d{6}\b", mail.outbox[-1].body).group(0)
        new = "Another-Pass-2468"
        r = self.client.post(reverse("accounts:password_reset_confirm"), {
            "email": "ivan@example.com",
            "code": reset_code,
            "new_password1": new,
            "new_password2": new,
        })
        self.assertRedirects(r, reverse("accounts:password_reset_complete"))

        self.assertNotIn("_auth_user_id", self.login(PASSWORD).client.session)
        self.assertRedirects(self.login(new), reverse("accounts:profile"), fetch_redirect_response=False)

    def test_password_reset_code_is_one_time_and_limited(self):
        self.register(); self.verify()
        self.client.post(reverse("accounts:password_reset"), {"email": "ivan@example.com"})
        code = re.search(r"\b\d{6}\b", mail.outbox[-1].body).group(0)
        new = "Another-Pass-2468"
        wrong_code = "000000" if code != "000000" else "000001"
        for _ in range(5):
            response = self.client.post(reverse("accounts:password_reset_confirm"), {
                "email": "ivan@example.com",
                "code": wrong_code,
                "new_password1": new,
                "new_password2": new,
            })
        self.assertContains(response, "Код недействителен")
        self.assertFalse(User.objects.get().check_password(new))

    @override_settings(PASSWORD_RESET_CODE_TTL=0)
    def test_expired_password_reset_code_is_rejected(self):
        self.register(); self.verify()
        self.client.post(reverse("accounts:password_reset"), {"email": "ivan@example.com"})
        code = re.search(r"\b\d{6}\b", mail.outbox[-1].body).group(0)
        new = "Another-Pass-2468"
        response = self.client.post(reverse("accounts:password_reset_confirm"), {
            "email": "ivan@example.com",
            "code": code,
            "new_password1": new,
            "new_password2": new,
        })
        self.assertContains(response, "Код недействителен")
        self.assertFalse(User.objects.get().check_password(new))
