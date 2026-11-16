"""Отправка оформленных HTML-писем в едином стиле проекта (шаблон emails/base.html)."""
from django.core.mail import EmailMessage
from django.template.loader import render_to_string


def html_email(subject, template, context, to, connection=None):
    """Собрать письмо, у которого тело — только HTML по шаблону."""
    message = EmailMessage(subject, render_to_string(template, context), None, list(to), connection=connection)
    message.content_subtype = "html"
    return message


def send_html_email(subject, template, context, to):
    return html_email(subject, template, context, to).send()
