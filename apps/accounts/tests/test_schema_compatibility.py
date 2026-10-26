from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import EmailVerificationCode, User
from apps.accounts.services import send_verification_email


class VerificationPageRegressionTests(TestCase):
    def test_code_can_be_confirmed_on_the_check_email_page(self):
        user = User.objects.create_user(email="confirmation@example.com", full_name="Confirmation")
        with patch("apps.accounts.services.secrets.randbelow", return_value=783512):
            send_verification_email(user)
        response = self.client.post(reverse("accounts:check_email"), {"email": user.email, "code": "783512"})
        self.assertContains(response, "Email подтверждён")
        user.refresh_from_db()
        self.assertTrue(user.is_email_verified)

    def test_check_email_contains_the_code_form_without_extra_navigation(self):
        response = self.client.get(reverse("accounts:check_email"))
        self.assertContains(response, 'name="code"')
        self.assertContains(response, 'name="email"')
        self.assertContains(response, "Подтвердить почту")

    def test_pending_address_from_previous_session_is_preserved(self):
        session = self.client.session
        session["pending_verification_email"] = "pending@example.com"
        session.save()
        response = self.client.get(reverse("accounts:check_email"))
        self.assertContains(response, "pending@example.com")
        self.assertEqual(self.client.post(reverse("accounts:check_email"), {"email": "pending@example.com", "code": "123456"}).status_code, 200)


class VerificationSchemaRegressionTests(TransactionTestCase):
    def test_old_branch_table_is_archived_and_new_codes_can_be_saved(self):
        if connection.vendor != "sqlite":
            self.skipTest("The affected local database uses SQLite.")
        user = User.objects.create_user(email="migration@example.com", full_name="Migration")
        table = EmailVerificationCode._meta.db_table
        archive = table + "_legacy_0002"
        migration = import_module("apps.accounts.migrations.0004_reconcile_verification_schema")
        try:
            with connection.schema_editor() as editor:
                editor.delete_model(EmailVerificationCode)
                editor.execute(
                    f'CREATE TABLE "{table}" ('
                    'id integer PRIMARY KEY, user_id bigint NOT NULL UNIQUE REFERENCES accounts_user(id), '
                    'code_hash varchar(64) NOT NULL, nonce char(32) NOT NULL, '
                    'expires_at datetime NULL, sent_at datetime NULL, attempts integer NOT NULL, '
                    'consumed_at datetime NULL, window_started_at datetime NOT NULL, send_count integer NOT NULL)'
                )
            with connection.cursor() as cursor:
                cursor.execute(
                    f'INSERT INTO "{table}" (id,user_id,code_hash,nonce,attempts,window_started_at,send_count) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                    [1, user.pk, "a" * 64, "b" * 32, 0, timezone.now(), 1],
                )
            with connection.schema_editor() as editor:
                migration.reconcile_schema(apps, editor)
                # Re-running reconciliation must be harmless.
                migration.reconcile_schema(apps, editor)
            self.assertEqual(EmailVerificationCode.objects.get(user=user).code_hash, "a" * 64)
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT COUNT(*) FROM "{archive}"')
                self.assertEqual(cursor.fetchone()[0], 1)
            another = User.objects.create_user(email="new-code@example.com", full_name="New")
            with patch("apps.accounts.services.send_mail", return_value=1):
                self.assertTrue(send_verification_email(another))
            self.assertTrue(EmailVerificationCode.objects.filter(user=another).exists())
            self.assertTrue(User.objects.filter(pk=user.pk).exists())
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP TABLE IF EXISTS "{archive}"')
