"""Final project report (ТЗ FR21, UC «Формирование итогового PDF-отчёта»).

The customer and the foreman build it; the executor has no access to money, so not to the report either (прил. А).
The report collects the project, stages and deadlines, the approved estimate with its approved changes, expenses,
completed works and remarks, and the final cost of the repair.
"""
from decimal import Decimal

from django.utils import timezone

from apps.estimates import services as estimate_services
from apps.estimates.models import ChangeRequest, Estimate, money
from apps.projects.models import ProjectMember
from apps.projects.permissions import require_manager
from apps.quality.models import Remark, StageDecision
from apps.repairs.models import Stage, Task
from apps.repairs.task_services import project_progress


def confirmed_expenses(project):
    """Confirmed expenses (FR12): only these are the actual costs; drafts, pending and rejected ones are left out.

    While there are none, the final cost is taken from the approved estimate together with its approved changes.
    """
    return list(project.expenses.filter(status="confirmed").select_related("stage").order_by("date", "number"))


def stage_rows(project, today):
    accepted_at = {}
    for decision in StageDecision.objects.filter(project=project, action=StageDecision.Action.ACCEPT).order_by("created_at"):
        accepted_at[decision.stage_id] = timezone.localtime(decision.created_at).date()
    progress = dict(project_progress(project)["stages"])
    rows = []
    for stage in project.stages.select_related("responsible__user").order_by("position", "start_date", "id"):
        finished = accepted_at.get(stage.pk) if stage.status == Stage.Status.ACCEPTED else None
        # Late: accepted after the deadline, or still not accepted when the deadline has passed.
        reference = finished or (today if stage.status != Stage.Status.CANCELLED else None)
        days_late = (reference - stage.end_date).days if reference and reference > stage.end_date else 0
        rows.append({"stage": stage, "accepted_at": finished, "days_late": days_late,
                     "progress": progress.get(stage, 100 if stage.status == Stage.Status.ACCEPTED else 0)})
    return rows


def schedule(project, stages):
    active = [row for row in stages if row["stage"].status != Stage.Status.CANCELLED]
    planned_start = project.start_date or min((row["stage"].start_date for row in active), default=None)
    planned_end = project.end_date or max((row["stage"].end_date for row in active), default=None)
    all_accepted = bool(active) and all(row["accepted_at"] for row in active)
    actual_end = max((row["accepted_at"] for row in active), default=None) if all_accepted else None
    return {"planned_start": planned_start, "planned_end": planned_end, "actual_end": actual_end,
            "days_late": (actual_end - planned_end).days if actual_end and planned_end else None,
            "late_stages": sum(1 for row in active if row["days_late"] > 0)}


def estimate_part(project):
    approved = estimate_services.approved_estimate(project)
    first = project.estimates.filter(status__in=[Estimate.Status.APPROVED, Estimate.Status.SUPERSEDED]).order_by("number").first()
    changes = list(project.change_requests.filter(status=ChangeRequest.Status.APPROVED).select_related("decided_by", "schedule_stage")
                   .prefetch_related("items").order_by("number"))
    return {"approved": approved, "summary": estimate_services.estimate_summary(approved) if approved else None,
            "initial_total": first.total if first else None, "changes": changes,
            "changes_total": money(sum((change.cost_delta for change in changes), Decimal("0")))}


def build_report(project, user):
    require_manager(project, user)
    today = timezone.localdate()
    stages = stage_rows(project, today)
    estimate = estimate_part(project)
    expenses = confirmed_expenses(project)
    members = list(project.members.filter(is_active=True).select_related("user"))
    done_tasks = list(project.tasks.filter(status=Task.Status.DONE).select_related("stage", "assignee__user", "approved_by")
                      .order_by("stage__position", "completed_at", "id"))
    remarks = list(Remark.objects.filter(project=project, withdrawn=False).select_related("stage", "responsible__user")
                   .order_by("created_at", "id"))
    if expenses:
        final_cost, cost_source = money(sum((expense.amount for expense in expenses), Decimal("0"))), "подтверждённым расходам"
    elif estimate["approved"] is not None:
        final_cost, cost_source = estimate["approved"].total, "утверждённой смете с учётом одобренных изменений"
    else:
        final_cost, cost_source = None, ""
    budget = project.budget
    return {
        "project": project, "generated_at": timezone.localtime(), "generated_by": user,
        "customer": next((m.user for m in members if m.role == ProjectMember.Role.CUSTOMER), None),
        "foreman": next((m.user for m in members if m.role == ProjectMember.Role.FOREMAN), None),
        "executors": [m.user for m in members if m.role == ProjectMember.Role.EXECUTOR],
        "progress": project_progress(project), "stages": stages, "schedule": schedule(project, stages),
        "estimate": estimate, "expenses": expenses,
        "done_tasks": done_tasks, "photo_reports": project.photo_reports.count(),
        "remarks": remarks, "remarks_open": sum(1 for r in remarks if r.status != Remark.Status.CLOSED),
        "final_cost": final_cost, "cost_source": cost_source, "budget": budget,
        "budget_left": (budget - final_cost) if (budget is not None and final_cost is not None) else None,
    }
