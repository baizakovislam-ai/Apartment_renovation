from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()
ICONS = {
    "home": '<path d="M3 10.8 12 3l9 7.8"/><path d="M5.5 9.5V21h13V9.5M9 21v-7h6v7"/>',
    "projects": '<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M8 5V3h8v2M3 10h18"/>',
    "stages": '<path d="M4 5h16M4 12h10M4 19h7"/><circle cx="19" cy="12" r="2"/><circle cx="16" cy="19" r="2"/>',
    "users": '<circle cx="9" cy="7" r="4"/><path d="M2 21v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2M17 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-4"/>',
    "history": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v6l4 2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "close": '<path d="m6 6 12 12M18 6 6 18"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    "arrow": '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    "alert": '<path d="m12 3 10 18H2Z M12 9v5M12 17h.01"/>',
}


@register.simple_tag
def icon(name):
    return format_html('<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>', mark_safe(ICONS.get(name, ICONS["projects"])))


@register.filter
def initials(user):
    name = user.get_full_name() or user.get_username()
    return "".join(part[0] for part in name.split()[:2]).upper()[:2]


@register.filter
def display_name(user):
    return user.get_full_name() or user.get_username()


@register.filter
def status_tone(status):
    return {"planned": "gray", "in_progress": "blue", "completed": "teal", "pending_acceptance": "orange", "accepted": "green", "cancelled": "red"}.get(status, "gray")


@register.filter
def event_label(action):
    return {"project.created": "Создан проект", "invitation.created": "Создано приглашение", "invitation.accepted": "Принято приглашение", "invitation.cancelled": "Отменено приглашение", "member.role_changed": "Изменена роль участника", "member.removed": "Удалён участник", "stage.created": "Создан этап", "stage.updated": "Обновлён этап", "stage.deleted": "Удалён черновик этапа"}.get(action, action)
