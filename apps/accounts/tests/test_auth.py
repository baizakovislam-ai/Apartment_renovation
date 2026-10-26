import re
from urllib.parse import urlparse

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import LoginAttempt, User

PASSWORD = "Strong-Pass-1957"
FAST_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def mail_path():
    """Путь ссылки из последнего письма."""
    return urlparse(re.search(r"https?://\S+", mail.outbox[-1].body).group(0)).path


def mail_code():
    return re.search(r"(?m)^[0-9]{6}$", mail.outbox[-1].body).group(0)


@override_settings(PASSWORD_HASHERS=FAST_HASHERS, LOGIN_MAX_ATTEMPTS=3)
class AuthFlowTests(TestCase):
    def register(self, email="Ivan@Example.com", password=PASSWORD):
        return self.client.post(reverse("accounts:register"), {
            "full_name": "Иван Петров", "email": email, "password1": password, "password2": password})

    def verify(self):
        self.assertRedirects(self.client.post(reverse("accounts:check_email"), {"code": mail_code()}), reverse("accounts:login"))

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

    def test_verify_then_login_and_profile(self):
        self.register()
        self.verify()
        r = self.login()
        self.assertRedirects(r, reverse("accounts:profile"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)

    def test_verification_code_single_use(self):
        self.register()
        from apps.accounts.services import VerificationError, confirm_verification_code
        code = mail_code()
        self.verify()
        with self.assertRaises(VerificationError):
            confirm_verification_code("ivan@example.com", code)

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
        r = self.client.get(mail_path(), follow=True)
        set_url = r.redirect_chain[-1][0]
        new = "Another-Pass-2468"
        r = self.client.post(set_url, {"new_password1": new, "new_password2": new})
        self.assertRedirects(r, reverse("accounts:password_reset_complete"))

        self.assertNotIn("_auth_user_id", self.login(PASSWORD).client.session)
        self.assertRedirects(self.login(new), reverse("accounts:profile"), fetch_redirect_response=False)
