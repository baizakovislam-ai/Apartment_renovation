from datetime import timedelta
from getpass import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.projects.models import Project
from apps.projects.services import accept_invitation, create_project, invite_member
from apps.repairs.services import create_stage


class Command(BaseCommand):
    help = "Create three demo accounts and a project with an invited team and a stage."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="Demo password (if omitted, ask without displaying it).")

    @transaction.atomic
    def handle(self, *args, **options):
        if Project.objects.filter(name="Демонстрационный ремонт", created_by__username="demo_customer").exists():
            self.stdout.write("Demo project already exists; no changes made.")
            return
        password = options["password"] or getpass("Password for demo accounts: ")
        if len(password) < 10:
            raise CommandError("Use a demo password of at least 10 characters.")
        User = get_user_model()
        users = []
        for username, email in [("demo_customer", "customer@example.com"), ("demo_foreman", "foreman@example.com"), ("demo_executor", "executor@example.com")]:
            if User.objects.filter(username=username).exists():
                raise CommandError(f"Account {username} already exists. Existing accounts are never overwritten.")
            users.append(User.objects.create_user(username=username, email=email, password=password))
        customer, foreman, executor = users
        project = create_project(user=customer, name="Демонстрационный ремонт")
        for user, role in [(foreman, "foreman"), (executor, "executor")]:
            invitation = invite_member(project=project, actor=customer, email=user.email, role=role)
            accept_invitation(token=invitation.token, user=user)
        today = timezone.localdate()
        create_stage(project=project, actor=customer, name="Подготовительные работы", description="Демонстрационный этап", start_date=today, end_date=today + timedelta(days=7), responsible=project.members.get(user=foreman))
        self.stdout.write(self.style.SUCCESS(f"Demo project ID: {project.pk}"))
        self.stdout.write("Accounts: demo_customer, demo_foreman, demo_executor")
        self.stdout.write("Sign in at /api/auth/login/ using the password you supplied.")
