from datetime import timedelta
from getpass import getpass
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.projects.services import accept_invitation, create_project, invite_member
from apps.quality.models import Remark, StageDecision
from apps.repairs.models import Stage, Task
from apps.repairs.services import create_stage


class Command(BaseCommand):
    help = "Create an isolated 40% demo with fixture stages already in progress; never modify existing accounts/projects."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="Password for three new demo accounts (otherwise ask privately).")

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        identities = [("Демо Заказчик", "customer40@example.com"), ("Демо Прораб", "foreman40@example.com"), ("Демо Исполнитель", "executor40@example.com")]
        if User.objects.filter(email__in=[email for _, email in identities]).exists():
            raise CommandError("Demo accounts already exist; no existing data was changed.")
        password = options["password"] or getpass("Password for new 40% demo accounts: ")
        if len(password) < 10:
            raise CommandError("Use at least 10 characters.")
        customer, foreman, executor = [User.objects.create_user(email=email, full_name=name, password=password, is_email_verified=True) for name, email in identities]
        project = create_project(user=customer, name="Демо 40% — ремонт квартиры", address="Бишкек, демонстрационная квартира",
            description="Учебные данные: этапы уже находятся в работе для проверки задач и приёмки. Запуск настоящего ремонта требует сметы и будет добавлен на этапе 60%.")
        for user, role in [(foreman, "foreman"), (executor, "executor")]:
            accept_invitation(token=invite_member(project=project, actor=customer, email=user.email, role=role).token, user=user)
        today = timezone.localdate()
        worker = project.members.get(user=executor)
        manager = project.members.get(user=foreman)
        for name, state, tasks in [
            ("Подготовка квартиры", "accepted", [("Защитить окна и двери", "done", -3)]),
            ("Электромонтаж", "in_progress", [("Проложить кабель на кухне", "in_progress", -1), ("Установить подрозетники", "todo", 3)]),
            ("Штукатурные работы", "in_progress", [("Выровнять стены в спальне", "done", 2)]),
            ("Чистовая отделка", "planned", [("Подготовить план покраски", "todo", 6)]),
        ]:
            stage = create_stage(project=project, actor=customer, name=name, responsible=manager, start_date=today - timedelta(days=7), end_date=today + timedelta(days=14))
            # Demo fixtures represent an existing repair, not a production launch shortcut.
            Stage.objects.filter(pk=stage.pk).update(status=state)
            for title, status, offset in tasks:
                Task.objects.create(project=project, stage=stage, name=title, description="Проверьте задание и добавьте результат с фотографиями.",
                    assignee=worker, due_date=today + timedelta(days=offset), status=status,
                    result="Работа выполнена и проверена." if status == "done" else "", request_id=uuid4(), created_by=foreman)
            if state == "accepted":
                StageDecision.objects.create(project=project, stage=stage, actor=customer, action="accept", reason="Ранее принятый этап: демонстрационные данные.")
            if name == "Электромонтаж":
                Remark.objects.create(project=project, stage=stage, description="Закрепить кабель у входа в кухню.", responsible=worker,
                    due_date=today + timedelta(days=2), request_id=uuid4(), created_by=customer)
        self.stdout.write(self.style.SUCCESS(f"Demo project: /projects/{project.pk}/"))
        self.stdout.write("Accounts: customer40@example.com, foreman40@example.com, executor40@example.com")
        self.stdout.write("Existing accounts and projects have not been modified.")
