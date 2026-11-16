from django.apps import AppConfig


class CommunicationsConfig(AppConfig):
    name = 'apps.communications'
    verbose_name = "Уведомления"

    def ready(self):
        from django.db.models.signals import post_save
        from apps.projects.models import ProjectEvent
        from .services import on_project_event
        post_save.connect(on_project_event, sender=ProjectEvent, dispatch_uid="notifications_from_project_events")
