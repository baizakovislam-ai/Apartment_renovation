from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.estimates.testing import mark_started
from apps.projects.models import ProjectMember
from apps.projects.services import create_project
from apps.repairs.models import Stage
from apps.repairs.services import create_stage, update_stage
from apps.repairs.task_services import create_task, project_progress, task_action
from .models import Remark, StageDecision


class AcceptanceAndRemarkTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        make = lambda email: User.objects.create_user(email=email, full_name=email.split("@")[0], is_email_verified=True)
        self.customer, self.foreman, self.executor, self.stranger = map(make, ["c@example.com", "f@example.com", "e@example.com", "s@example.com"])
        self.project = create_project(user=self.customer, name="Ремонт")
        mark_started(self.project)  # works started: estimate approved (ТЗ 4.2)
        self.foreman_member = ProjectMember.objects.create(project=self.project, user=self.foreman, role="foreman")
        self.executor_member = ProjectMember.objects.create(project=self.project, user=self.executor, role="executor")
        today = timezone.localdate()
        self.stage = create_stage(project=self.project, actor=self.foreman, name="Штукатурка", start_date=today, end_date=today + timedelta(days=5), responsible=self.foreman_member)
        update_stage(stage=self.stage, actor=self.foreman, status="in_progress")
        self.task = create_task(project=self.project, actor=self.foreman, stage=self.stage, name="Стены", assignee=self.executor_member, due_date=today)
        self.acceptance = f"/api/stages/{self.stage.pk}/acceptance/"
        self.remarks = f"/api/projects/{self.project.pk}/remarks/"
        self.due = str(today + timedelta(days=2))

    def finish_task(self):
        for action, user, text in [("start", self.executor, ""), ("submit", self.executor, "Готово"), ("approve", self.foreman, "")]:
            self.task.refresh_from_db()
            task_action(task=self.task, actor=user, action=action, version=self.task.version, text=text)

    def post(self, user, url, data):
        self.client.force_authenticate(user)
        return self.client.post(url, data)

    def remark_action(self, user, remark_id, action, text=""):
        version = Remark.objects.get(pk=remark_id).version
        return self.post(user, f"/api/remarks/{remark_id}/action/", {"action": action, "version": version, "text": text})

    def test_submit_is_blocked_until_tasks_are_done_and_only_foreman_submits(self):
        self.assertEqual(self.post(self.foreman, self.acceptance, {"action": "submit"}).status_code, 400)
        self.finish_task()
        self.assertEqual(self.post(self.customer, self.acceptance, {"action": "submit"}).status_code, 403)
        self.assertEqual(self.post(self.foreman, self.acceptance, {"action": "submit"}).data["status"], "pending_acceptance")
        self.assertEqual(self.post(self.foreman, self.acceptance, {"action": "accept"}).status_code, 403)

    def test_customer_accepts_and_decision_is_stored(self):
        self.finish_task()
        self.post(self.foreman, self.acceptance, {"action": "submit"})
        self.assertEqual(self.post(self.customer, self.acceptance, {"action": "accept"}).data["status"], "accepted")
        decision = StageDecision.objects.get(action="accept")
        self.assertEqual(decision.actor, self.customer)
        self.assertEqual(project_progress(self.project)["percent"], 100)
        # Repeated click is rejected instead of being applied twice.
        self.assertEqual(self.post(self.customer, self.acceptance, {"action": "accept"}).status_code, 400)
        self.assertEqual(StageDecision.objects.filter(action="accept").count(), 1)

    def test_return_requires_reason(self):
        self.finish_task()
        self.post(self.foreman, self.acceptance, {"action": "submit"})
        self.assertEqual(self.post(self.customer, self.acceptance, {"action": "return"}).status_code, 400)
        self.assertEqual(self.post(self.customer, self.acceptance, {"action": "return", "reason": "Неровные углы"}).data["status"], "in_progress")

    def test_remark_returns_stage_and_blocks_acceptance_until_closed(self):
        self.finish_task()
        self.post(self.foreman, self.acceptance, {"action": "submit"})
        payload = {"stage": self.stage.pk, "description": "Трещина у окна", "responsible": self.executor_member.pk, "due_date": self.due}
        self.assertEqual(self.post(self.executor, self.remarks, payload).status_code, 403)
        remark = self.post(self.customer, self.remarks, payload).data
        self.stage.refresh_from_db()
        self.assertEqual(self.stage.status, Stage.Status.IN_PROGRESS)
        self.assertIn("Открытых замечаний: 1", self.post(self.foreman, self.acceptance, {"action": "submit"}).data[0])
        # Executor answers and reports the fix; only the customer can close.
        self.assertEqual(self.remark_action(self.executor, remark["id"], "reply", "Заделаю завтра").status_code, 200)
        self.assertEqual(self.remark_action(self.executor, remark["id"], "fix").data["status"], "fixed")
        self.assertEqual(self.remark_action(self.executor, remark["id"], "close").status_code, 403)
        self.assertEqual(self.post(self.foreman, self.acceptance, {"action": "submit"}).status_code, 400)  # fixed still blocks
        self.assertEqual(self.remark_action(self.customer, remark["id"], "reopen").status_code, 400)  # reason required
        self.assertEqual(self.remark_action(self.customer, remark["id"], "reopen", "Трещина осталась").data["status"], "open")
        self.remark_action(self.executor, remark["id"], "fix")
        closed = self.remark_action(self.customer, remark["id"], "close")
        self.assertEqual(closed.data["status"], "closed")
        self.assertEqual(len(closed.data["comments"]), 2)
        self.assertEqual(self.post(self.foreman, self.acceptance, {"action": "submit"}).status_code, 200)

    def test_remark_visibility_and_validation(self):
        payload = {"stage": self.stage.pk, "description": "Пятно", "responsible": self.foreman_member.pk, "due_date": self.due}
        self.post(self.customer, self.remarks, payload)
        self.client.force_authenticate(self.executor)
        self.assertEqual(self.client.get(self.remarks).data, [])
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get(self.remarks).status_code, 404)
        past = {**payload, "due_date": str(timezone.localdate() - timedelta(days=1))}
        self.assertEqual(self.post(self.customer, self.remarks, past).status_code, 400)
        customer_member = self.project.members.get(user=self.customer)
        self.assertEqual(self.post(self.customer, self.remarks, {**payload, "responsible": customer_member.pk}).status_code, 400)

    def test_stale_remark_version_is_rejected(self):
        remark = self.post(self.customer, self.remarks, {"stage": self.stage.pk, "description": "Пятно", "responsible": self.executor_member.pk, "due_date": self.due}).data
        self.remark_action(self.executor, remark["id"], "fix")
        stale = self.post(self.customer, f"/api/remarks/{remark['id']}/action/", {"action": "close", "version": remark["version"]})
        self.assertEqual(stale.status_code, 400)
