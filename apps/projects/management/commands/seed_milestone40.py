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

from apps.estimates.services import (add_item, add_line, approve_estimate, create_calculation, create_draft,
                                     start_works, submit_estimate)
from apps.projects.models import Project
from apps.projects.services import record_event, update_project
from apps.quality.services import accept_stage, create_remark, submit_stage
from apps.repairs.services import create_stage, update_stage
from apps.repairs.models import Task
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

    def plan(name, start, end, position):
        return create_stage(project=project, actor=foreman, name=name, start_date=today + timedelta(days=start),
                            end_date=today + timedelta(days=end), responsible=foreman_member, position=position)

    def start(stage_obj):
        update_stage(stage=stage_obj, actor=foreman, status="in_progress")
        return stage_obj

    def task(stage_obj, name, due, description=""):
        due_date = today + timedelta(days=due)
        if due_date >= today:
            return create_task(project=project, actor=foreman, stage=stage_obj, name=name, assignee=executor_member,
                               due_date=due_date, description=description)
        # Demo history: the service refuses deadlines in the past, so past tasks are written directly.
        created = Task.objects.create(project=project, stage=stage_obj, name=name, assignee=executor_member,
                                      due_date=due_date, description=description, created_by=foreman)
        record_event(project, foreman, "task.created", task_id=created.pk, name=name, assignee_id=executor.pk, due_date=str(due_date))
        return created

    # 0. Plan, preliminary calculation, estimate and its approval — ТЗ scenario 4.2 before any work starts.
    demolition = plan("Демонтаж", -20, -12, 2)
    electric = plan("Электромонтаж", -6, 6, 3)
    plaster = plan("Штукатурка стен", -4, 10, 4)
    finishing = plan("Чистовая отделка", 11, 40, 5)
    calculation = create_calculation(project=project, actor=customer, name="Стандартный ремонт 64,5 м²",
                                     note="Ориентировочно, по средним ценам Бишкека.")
    for work, unit, volume, price in [("Демонтаж покрытий", "м²", "64.5", "250"), ("Электромонтаж под ключ", "м²", "64.5", "900"),
                                      ("Штукатурка стен по маякам", "м²", "180", "450"), ("Чистовая отделка", "м²", "64.5", "2200")]:
        add_item(calculation=calculation, actor=customer, work=work, unit=unit, volume=volume, price=price)
    estimate = create_draft(project=project, actor=foreman)
    for stage_obj, name, cost_type, unit, quantity, price in [
            (demolition, "Демонтаж плитки и обоев", "work", "м²", "120", "220"),
            (demolition, "Вывоз строительного мусора", "delivery", "рейс", "3", "4500"),
            (electric, "Прокладка кабеля", "work", "м", "260", "120"),
            (electric, "Кабель ВВГнг 3×2,5", "material", "м", "260", "85"),
            (electric, "Сборка электрощитка", "work", "шт", "1", "8000"),
            (plaster, "Штукатурка стен по маякам", "work", "м²", "180", "450"),
            (plaster, "Гипсовая штукатурка 30 кг", "material", "мешок", "60", "520"),
            (finishing, "Укладка ламината", "work", "м²", "48", "650"),
            (finishing, "Ламинат 33 класс", "material", "м²", "52", "1450")]:
        estimate.refresh_from_db()
        add_line(estimate=estimate, actor=foreman, revision=estimate.revision, stage=stage_obj, name=name,
                 cost_type=cost_type, unit=unit, quantity=quantity, price=price)
    estimate.refresh_from_db()
    submit_estimate(estimate=estimate, actor=foreman, comment="Смета по замерам, цены на материалы на текущую неделю.")
    approve_estimate(estimate=estimate, actor=customer)
    start_works(project=project, actor=customer)

    # 1. Demolition: fully done and accepted by the customer.
    start(demolition)
    walls = task(demolition, "Снять старую плитку и обои", -14)
    run(walls, foreman, ("start", ""))
    add_photo_report(task=walls, actor=executor, description="Стены очищены до бетона.", images=[demo_photo("Demontazh", (120, 98, 80))])
    run(walls, foreman, ("submit", "Плитка и обои сняты, мусор вывезен."), ("approve", ""))
    submit_stage(stage=demolition, actor=foreman)
    accept_stage(stage=demolition, actor=customer)

    # 2. Electrical: tasks in every status, one overdue, plus an open remark.
    start(electric)
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
    start(plaster)
    plaster_task = task(plaster, "Выровнять стены в гостиной", 3)
    run(plaster_task, foreman, ("start", ""))
    add_photo_report(task=plaster_task, actor=executor, description="Стены выровнены по маякам.", images=[demo_photo("Shtukaturka", (150, 150, 140))])
    run(plaster_task, foreman, ("submit", "Отклонение не более 2 мм на 2 м."), ("approve", ""))
    # 4. Finishing stays planned.


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
