from django.core.management.base import BaseCommand

from apps.communications.services import check_deadlines


class Command(BaseCommand):
    help = "Напомнить о приближающихся и просроченных сроках задач, замечаний и этапов (FR19). Повторный запуск не дублирует напоминания."

    def handle(self, *args, **options):
        created = check_deadlines()
        self.stdout.write(self.style.SUCCESS(f"Создано напоминаний: {len(created)}"))
