from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """Токен подтверждения email. Становится недействительным после подтверждения
    (в хеш входит is_email_verified), срок жизни — settings.PASSWORD_RESET_TIMEOUT."""

    key_salt = "apps.accounts.tokens.EmailVerificationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.email}{user.is_email_verified}{timestamp}"


email_verification_token = EmailVerificationTokenGenerator()
