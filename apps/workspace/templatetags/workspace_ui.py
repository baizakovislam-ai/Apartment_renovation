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
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
    "tasks": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="m8 9 1.5 1.5L12 8M8 15l1.5 1.5L12 14M14 9.5h3M14 15.5h3"/>',
    "flag": '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
    "camera": '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>',
    "money": '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6.5 9.5v5M17.5 9.5v5"/>',
    "calc": '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8.5 11h.01M12 11h.01M15.5 11h.01M8.5 14.5h.01M12 14.5h.01M15.5 14.5v3M8.5 17.5h.01M12 17.5h.01"/>',
    "stamp": '<path d="M9 4h6v5l2 3H7l2-3zM5 15h14v3H5zM7 21h10"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "logout": '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4M6 12h10"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    "send": '<path d="m4 12 16-8-6 16-2.5-6.5z"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "play": '<path d="M7 5v14l12-7z"/>',
}


@register.simple_tag
def icon(name, size=18):
    return format_html('<svg width="{}" height="{}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>', size, size, mark_safe(ICONS.get(name, ICONS["projects"])))


@register.filter
def initials(user):
    name = user.get_full_name() or user.get_username()
    return "".join(part[0] for part in name.split()[:2]).upper()[:2]


@register.filter
def display_name(user):
    return user.get_full_name() or user.get_username()


@register.filter
def status_tone(status):
    return {"planned": "gray", "in_progress": "blue", "completed": "teal", "pending_acceptance": "orange", "accepted": "green", "cancelled": "red",
            "todo": "gray", "review": "orange", "done": "green", "open": "red", "fixed": "orange", "closed": "green",
            "planning": "gray", "draft": "gray", "pending": "orange", "approved": "green", "rejected": "red", "superseded": "gray"}.get(status, "gray")


@register.filter
def money(value):
    """12345.5 -> '12 345,50 сом' (ТЗ: денежная единица — кыргызский сом)."""
    if value in (None, ""):
        return "—"
    whole, _, frac = f"{value:,.2f}".partition(".")
    return f"{whole.replace(',', ' ')},{frac} сом"


@register.filter
def decision_tone(action):
    return {"submit": "orange", "accept": "green", "return": "red"}.get(action, "gray")


@register.filter
def event_label(action):
    return {"project.created": "Создан проект", "invitation.created": "Создано приглашение", "invitation.accepted": "Принято приглашение", "invitation.cancelled": "Отменено приглашение", "member.role_changed": "Изменена роль участника", "member.removed": "Удалён участник", "stage.created": "Создан этап", "stage.updated": "Обновлён этап", "stage.deleted": "Удалён черновик этапа",
            "project.updated": "Изменены данные проекта",
            "task.created": "Создана задача", "task.updated": "Изменена задача", "task.deleted": "Удалена задача",
            "task.start": "Задача взята в работу", "task.submit": "Задача передана на проверку", "task.approve": "Выполнение задачи подтверждено",
            "task.return": "Задача возвращена на доработку", "photo_report.created": "Добавлен фотоотчёт",
            "stage.submit": "Этап передан на приёмку", "stage.accept": "Этап принят", "stage.return": "Этап возвращён на доработку",
            "remark.created": "Создано замечание", "remark.reply": "Ответ на замечание", "remark.fix": "Замечание отмечено устранённым",
            "remark.close": "Устранение подтверждено", "remark.reopen": "Замечание возвращено на доработку",
            "remark.updated": "Замечание изменено", "remark.withdraw": "Замечание снято",
            "project.archived": "Проект отправлен в архив", "project.restored": "Проект возвращён из архива",
            "invitation.declined": "Приглашение отклонено",
            "estimate.created": "Создана версия сметы", "estimate.line_added": "Добавлена строка сметы",
            "estimate.line_updated": "Изменена строка сметы", "estimate.line_deleted": "Удалена строка сметы",
            "estimate.submitted": "Смета отправлена на согласование", "estimate.withdrawn": "Смета отозвана в черновик",
            "estimate.approved": "Смета утверждена", "estimate.rejected": "Смета отклонена",
            "calculation.created": "Создан предварительный расчёт", "calculation.deleted": "Удалён предварительный расчёт",
            "calculation.to_estimate": "Расчёт перенесён в смету", "project.started": "Работы по проекту запущены"}.get(action, action)


@register.filter
def normalize(value):
    """120.000 -> 120, 2.500 -> 2,5 (quantities without trailing zeros, Russian decimal comma)."""
    if value in (None, ""):
        return "—"
    text = format(value.normalize(), "f") if hasattr(value, "normalize") else str(value)
    return text.replace(".", ",")


@register.filter
def highlight(text, query):
    """Wrap every search stem found in the text in <mark> (the text is escaped first)."""
    import re
    from django.utils.html import escape
    from apps.estimates.search import search_terms
    text = escape(str(text))
    terms = [escape(term) for term in search_terms(query)]
    if not terms:
        return mark_safe(text)
    pattern = "|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True))
    return mark_safe(re.sub(pattern, lambda match: f"<mark>{match.group(0)}</mark>", text, flags=re.IGNORECASE))
