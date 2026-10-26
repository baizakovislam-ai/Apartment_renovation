"""Adds 40% milestone demo content (tasks, photo reports, acceptance, remarks) to the demo project.

Everything is created through the service layer, so all business rules and history apply.
"""
import io
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw

from apps.projects.models import Project
from apps.projects.services import update_project
from apps.quality.services import accept_stage, create_remark, submit_stage
from apps.repairs.services import create_stage, update_stage
from apps.repairs.task_services import add_photo_report, create_task, task_action

DEMO_NAME = "Демонстрационный ремонт"


def demo_photo(label, color):
    image = Image.new("RGB", (640, 420), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle([30, 30, 610, 390], outline=(255, 255, 255), width=6)
    draw.text((50, 50), label, fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return SimpleUploadedFile("demo.jpg", buffer.getvalue(), content_type="image/jpeg")


def run(task, reviewer, *steps):
    """Executor starts/submits, the reviewer approves or returns."""
    for action, text in steps:
        task.refresh_from_db()
        actor = task.assignee.user if action in ("start", "submit") else reviewer
        task_action(task=task, actor=actor, action=action, version=task.version, text=text)


def fill_demo(project, customer, foreman):
    executor_member = project.members.get(role="executor")
    executor = executor_member.user
    foreman_member = project.members.get(role="foreman")
    today = timezone.localdate()
    update_project(project=project, actor=customer, address="г. Бишкек, ул. Киевская, 120, кв. 45", area=64.5,
                   start_date=today - timedelta(days=20), end_date=today + timedelta(days=60), budget=850000,
                   description="Капитальный ремонт двухкомнатной квартиры: демонтаж, электрика, штукатурка, чистовая отделка.")

    def stage(name, start, end, position):
        created = create_stage(project=project, actor=foreman, name=name, start_date=today + timedelta(days=start),
                               end_date=today + timedelta(days=end), responsible=foreman_member, position=position)
        update_stage(stage=created, actor=foreman, status="in_progress")
        return created

    def task(stage_obj, name, due, description=""):
        return create_task(project=project, actor=foreman, stage=stage_obj, name=name, assignee=executor_member,
                           due_date=today + timedelta(days=due), description=description)

    # 1. Demolition: fully done and accepted by the customer.
    demolition = stage("Демонтаж", -20, -12, 2)
    walls = task(demolition, "Снять старую плитку и обои", -14)
    run(walls, foreman, ("start", ""))
    add_photo_report(task=walls, actor=executor, description="Стены очищены до бетона.", images=[demo_photo("Demontazh", (120, 98, 80))])
    run(walls, foreman, ("submit", "Плитка и обои сняты, мусор вывезен."), ("approve", ""))
    submit_stage(stage=demolition, actor=foreman)
    accept_stage(stage=demolition, actor=customer)

    # 2. Electrical: tasks in every status, one overdue, plus an open remark.
    electric = stage("Электромонтаж", -6, 6, 3)
    done = task(electric, "Проложить кабель в кухне", -3)
    run(done, foreman, ("start", ""))
    add_photo_report(task=done, actor=executor, description="Кабель уложен в гофру.", images=[demo_photo("Kabel", (40, 90, 160))])
    run(done, foreman, ("submit", "Кабель проложен, сопротивление изоляции проверено."), ("approve", ""))
    review = task(electric, "Собрать электрощиток", 2, "Автоматы по схеме, маркировка линий.")
    run(review, foreman, ("start", ""), ("submit", "Щиток собран, линии подписаны."))
    overdue = task(electric, "Установить подрозетники", -1)
    run(overdue, foreman, ("start", ""))
    task(electric, "Подключить освещение", 5)
    create_remark(project=project, actor=customer, stage=electric, task=done, description="Кабель в коридоре не закреплён к потолку.",
                  responsible=executor_member, due_date=today + timedelta(days=2))

    # 3. Plastering: all tasks approved, the foreman can hand it over.
    plaster = stage("Штукатурка стен", -4, 10, 4)
    plaster_task = task(plaster, "Выровнять стены в гостиной", 3)
    run(plaster_task, foreman, ("start", ""))
    add_photo_report(task=plaster_task, actor=executor, description="Стены выровнены по маякам.", images=[demo_photo("Shtukaturka", (150, 150, 140))])
    run(plaster_task, foreman, ("submit", "Отклонение не более 2 мм на 2 м."), ("approve", ""))

    # 4. Finishing: planned, not started yet.
    create_stage(project=project, actor=foreman, name="Чистовая отделка", start_date=today + timedelta(days=11),
                 end_date=today + timedelta(days=40), responsible=foreman_member, position=5)


class Command(BaseCommand):
    help = "Fill the demo project with tasks, photo reports, acceptance and remarks (40% milestone)."

    @transaction.atomic
    def handle(self, *args, **options):
        project = Project.objects.filter(name=DEMO_NAME, created_by__email="customer@example.com").first()
        if project is None:
            raise CommandError("Demo project not found. Run `manage.py seed_demo` first.")
        if project.tasks.exists():
            self.stdout.write("Demo project already has tasks; no changes made.")
            return
        if not project.members.filter(role="foreman").exists() or not project.members.filter(role="executor").exists():
            raise CommandError("Demo project needs a foreman and an executor.")
        fill_demo(project, project.created_by, project.members.get(role="foreman").user)
        self.stdout.write(self.style.SUCCESS(f"Demo project {project.pk} filled with 40% milestone data."))
