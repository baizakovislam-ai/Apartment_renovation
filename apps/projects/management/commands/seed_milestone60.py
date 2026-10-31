"""Adds the 60 % demo content of the estimate changes (ТЗ FR11) to the demo project.

The foreman proposes a change of the approved estimate: a material got more expensive, one position is added and the
finishing stage moves by three days. The request waits for the customer, so the customer sees it on «Главная».
Fresh databases get the same content from `seed_demo`; this command is for a demo project created before.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.estimates import change_services
from apps.estimates.services import approved_estimate
from apps.projects.models import Project

DEMO_NAME = "Демонстрационный ремонт"


def fill_changes(project):
    """Create one pending change request by the foreman. Returns it, or None when there is nothing to change."""
    base = approved_estimate(project)
    foreman = project.members.filter(role="foreman", is_active=True).select_related("user").first()
    if base is None or foreman is None or project.change_requests.exists():
        return None
    lines = list(base.lines.select_related("stage"))
    material = next((line for line in lines if line.cost_type == "material"), None) or (lines[0] if lines else None)
    stage = project.stages.exclude(status__in=["accepted", "cancelled"]).order_by("-position").first()
    change = change_services.create_request(
        project=project, actor=foreman.user,
        reason="Поставщик поднял цены на материалы с 1 числа; заказчик попросил добавить тёплый пол в ванной.",
        schedule_stage=stage, new_end_date=(stage.end_date + timedelta(days=3)) if stage else None)
    if material is not None:
        change.refresh_from_db()
        change_services.set_item(request=change, actor=foreman.user, revision=change.revision, action="update", line=material,
                                 price=str(round(material.price * 112 / 100, 2)))
    if stage is not None:
        change.refresh_from_db()
        change_services.set_item(request=change, actor=foreman.user, revision=change.revision, action="add", stage=stage,
                                 name="Тёплый пол (нагревательный мат)", cost_type="equipment", unit="м²", quantity="4", price="2600")
    change.refresh_from_db()
    return change_services.submit_request(request=change, actor=foreman.user, revision=change.revision)


class Command(BaseCommand):
    help = "Add a pending estimate change request (60% milestone, FR11) to the existing demo project."

    @transaction.atomic
    def handle(self, *args, **options):
        project = Project.objects.filter(name=DEMO_NAME, created_by__email="customer@example.com").first()
        if project is None:
            raise CommandError("Demo project not found. Run `manage.py seed_demo` first.")
        if project.change_requests.exists():
            self.stdout.write("Demo project already has estimate change requests; no changes made.")
            return
        if approved_estimate(project) is None:
            raise CommandError("Demo project has no approved estimate. Run `manage.py seed_milestone80` first.")
        change = fill_changes(project)
        self.stdout.write(self.style.SUCCESS(f"Demo project {project.pk}: estimate change request №{change.number} is waiting for the customer."))
