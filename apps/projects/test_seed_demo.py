import io
import shutil
import tempfile

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.projects.models import Project

TEMP_MEDIA = tempfile.mkdtemp()


@override_settings(PRIVATE_MEDIA_ROOT=TEMP_MEDIA, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SeedDemoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def test_seed_demo_builds_project_for_three_roles(self):
        call_command("seed_demo", password="Demo-password-2026", stdout=io.StringIO())
        project = Project.objects.get(name="Демонстрационный ремонт")
        statuses = set(project.tasks.values_list("status", flat=True))
        self.assertEqual(statuses, {"todo", "in_progress", "review", "done"})
        self.assertTrue(project.stages.filter(status="accepted").exists())
        self.assertTrue(project.remarks.filter(status="open").exists())
        self.assertEqual(project.photo_reports.count(), 3)
        call_command("seed_milestone40", stdout=io.StringIO())  # second run changes nothing
        self.assertEqual(project.tasks.count(), 6)

    def test_seed_milestone80_adds_estimate_history_to_old_demo(self):
        call_command("seed_demo", password="Demo-password-2026", stdout=io.StringIO())
        project = Project.objects.get(name="Демонстрационный ремонт")
        project.change_requests.all().delete()
        project.estimates.all().delete()  # simulate a demo created before the estimate module
        project.calculations.all().delete()
        call_command("seed_milestone80", stdout=io.StringIO())
        statuses = list(project.estimates.order_by("number").values_list("status", flat=True))
        self.assertEqual(statuses, ["rejected", "approved"])
        self.assertEqual(project.calculations.count(), 1)
        call_command("seed_milestone80", stdout=io.StringIO())  # repeat run changes nothing
        self.assertEqual(project.estimates.count(), 2)

    def test_seed_milestone60_adds_pending_estimate_change(self):
        call_command("seed_demo", password="Demo-password-2026", stdout=io.StringIO())
        project = Project.objects.get(name="Демонстрационный ремонт")
        change = project.change_requests.get()
        self.assertEqual((change.status, change.items.count()), ("pending", 2))
        self.assertGreater(change.cost_delta, 0)
        project.change_requests.all().delete()  # simulate a demo created before the estimate changes
        call_command("seed_milestone60", stdout=io.StringIO())
        self.assertEqual(project.change_requests.count(), 1)
        call_command("seed_milestone60", stdout=io.StringIO())  # repeat run changes nothing
        self.assertEqual(project.change_requests.count(), 1)
