from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.projects.models import Project
from apps.repairs.models import Task


class MilestoneDemoTests(TestCase):
    def test_demo_is_repeat_safe_and_does_not_change_existing_accounts(self):
        call_command('seed_milestone40', password='Demo-Workflow-40!', stdout=StringIO())
        User = get_user_model()
        original = list(User.objects.order_by('id').values_list('id', 'email', 'password'))
        self.assertEqual(len(original), 3)
        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(Task.objects.count(), 5)
        self.assertEqual(Project.objects.get().stages.filter(status='accepted').count(), 1)
        with self.assertRaises(CommandError):
            call_command('seed_milestone40', password='Different-Password!', stdout=StringIO())
        self.assertEqual(list(User.objects.order_by('id').values_list('id', 'email', 'password')), original)
        self.assertEqual(Project.objects.count(), 1)
