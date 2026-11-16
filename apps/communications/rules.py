"""Who is told about what (ТЗ FR19).

Every change in a project is written to the history (ProjectEvent). A rule turns an event into
notifications: it decides the recipients, the text and the page the notification opens.
The author of the change is never notified about their own action.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.urls import reverse

from apps.projects.models import ProjectMember

CUSTOMER, FOREMAN, EXECUTOR = ProjectMember.Role.CUSTOMER, ProjectMember.Role.FOREMAN, ProjectMember.Role.EXECUTOR


@dataclass
class Note:
    user: object
    kind: str
    title: str
    text: str = ""
    url: str = ""


def name_of(user):
    return (user.get_full_name() or user.email) if user else "Участник"


def members(project, *roles):
    return [member.user for member in project.members.filter(is_active=True, role__in=roles).select_related("user")]


def member_user(project, user_id):
    """The user, if they are still an active member of the project (removed members get nothing)."""
    member = project.members.filter(is_active=True, user_id=user_id).select_related("user").first()
    return member.user if member else None


def money(value):
    """320000.00 → «320 000 сом», 1500.5 → «1 500,50 сом»."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return f"{value} сом"
    text = f"{abs(number):,.2f}".replace(",", " ").replace(".", ",").removesuffix(",00")
    return f"{'−' if number < 0 else ''}{text} сом"


def signed(value):
    text = money(value)
    return text if text.startswith("−") or text == "0 сом" else f"+{text}"


def url(name, *args):
    return reverse(f"workspace:{name}", args=args)


def get(model, pk):
    return model.objects.filter(pk=pk).first() if pk else None


# ---------- Tasks ----------

def task_rule(event, project):
    from apps.repairs.models import Task
    data, action = event.data, event.action.split(".", 1)[1]
    actor = name_of(event.actor)
    task = get(Task, data.get("task_id"))
    name = data.get("name", "")
    if action == "deleted":
        user = member_user(project, data.get("assignee_id"))
        return [Note(user, "task", f"Задача «{name}» удалена", f"{actor} удалил(а) задачу.", url("tasks", project.pk))] if user else []
    if task is None:
        return []
    link, assignee = url("task", project.pk, task.pk), task.assignee.user if task.assignee.is_active else None
    due = f"Срок: {task.due_date:%d.%m.%Y}."
    if action == "created":
        return [Note(assignee, "task", f"Вам назначена задача «{task.name}»", f"{actor} назначил(а) задачу в этапе «{task.stage.name}». {due}", link)]
    if action == "updated":
        notes = []
        old_user = member_user(project, data.get("old_assignee_id"))
        if old_user and old_user != assignee:
            notes.append(Note(old_user, "task", f"Задача «{task.name}» передана другому исполнителю", f"{actor} сменил(а) исполнителя.", link))
            notes.append(Note(assignee, "task", f"Вам назначена задача «{task.name}»", f"{actor} передал(а) вам задачу. {due}", link))
        else:
            changes = describe_task_changes(data.get("after", {}))
            notes.append(Note(assignee, "task", f"Изменена задача «{task.name}»", f"{actor} изменил(а) {changes}. {due}", link))
        return notes
    if action == "submit":
        return [Note(user, "approval", f"Задача «{task.name}» ждёт проверки", f"{actor} передал(а) задачу на проверку.", link)
                for user in members(project, CUSTOMER, FOREMAN)]
    if action == "approve":
        return [Note(assignee, "task", f"Задача «{task.name}» принята", f"{actor} подтвердил(а) выполнение.", link)]
    if action == "return":
        return [Note(assignee, "task", f"Задача «{task.name}» возвращена на доработку", f"{actor}: «{data.get('text', '')}»", link)]
    return []


def describe_task_changes(after):
    labels = {"due_date": "срок", "name": "название", "description": "описание", "stage": "этап", "assignee": "исполнителя"}
    names = [labels[key] for key in after if key in labels]
    return ", ".join(names) if names else "задачу"


# ---------- Remarks and acceptance ----------

def remark_rule(event, project):
    from apps.quality.models import Remark
    action = event.action.split(".", 1)[1]
    remark = get(Remark, event.data.get("remark_id"))
    if remark is None:
        return []
    actor, link = name_of(event.actor), url("remark", project.pk, remark.pk)
    short = remark.description if len(remark.description) <= 60 else remark.description[:57] + "…"
    responsible = remark.responsible.user if remark.responsible.is_active else None
    author = member_user(project, remark.created_by_id)
    if action == "created":
        notes = [Note(responsible, "remark", f"Новое замечание: «{short}»",
                      f"{actor} оставил(а) замечание к этапу «{remark.stage.name}». Устранить до {remark.due_date:%d.%m.%Y}.", link)]
        if remark.responsible.role == EXECUTOR:  # the foreman keeps track of executors' remarks
            notes += [Note(user, "remark", f"Замечание исполнителю: «{short}»", f"{actor} оставил(а) замечание к этапу «{remark.stage.name}».", link)
                      for user in members(project, FOREMAN)]
        return notes
    if action == "updated":
        return [Note(responsible, "remark", f"Изменено замечание «{short}»", f"{actor} изменил(а) замечание. Срок: {remark.due_date:%d.%m.%Y}.", link)]
    if action == "reply":
        return [Note(user, "remark", f"Ответ по замечанию «{short}»", f"{actor} ответил(а) в обсуждении.", link) for user in (author, responsible)]
    if action == "fix":
        return [Note(author, "approval", f"Замечание устранено: «{short}»", f"{actor} сообщил(а) об устранении. Проверьте и подтвердите.", link)]
    titles = {"close": ("Замечание закрыто", "подтвердил(а) устранение"), "reopen": ("Замечание возвращено", "сообщил(а), что замечание не устранено"),
              "withdraw": ("Замечание снято", "снял(а) замечание")}
    if action in titles:
        title, verb = titles[action]
        return [Note(responsible, "remark", f"{title}: «{short}»", f"{actor} {verb}.", link)]
    return []


def stage_rule(event, project):
    from apps.repairs.models import Stage
    action = event.action.split(".", 1)[1]
    stage = get(Stage, event.data.get("stage_id"))
    if stage is None or action not in ("submit", "accept", "return"):
        return []
    actor, link = name_of(event.actor), url("acceptance", project.pk)
    if action == "submit":
        return [Note(user, "approval", f"Этап «{stage.name}» передан на приёмку", f"{actor} передал(а) этап. Проверьте работы и примите этап.", link)
                for user in members(project, CUSTOMER)]
    responsible = stage.responsible.user if stage.responsible.is_active else None
    users = [*members(project, FOREMAN), responsible]
    if action == "accept":
        return [Note(user, "approval", f"Этап «{stage.name}» принят", f"{actor} принял(а) этап.", link) for user in users]
    reason = event.data.get("reason", "")
    return [Note(user, "approval", f"Этап «{stage.name}» возвращён на доработку", f"{actor}: «{reason}»" if reason else f"{actor} вернул(а) этап.", link)
            for user in users]


# ---------- Approvals: estimate, estimate changes, expenses ----------

def estimate_rule(event, project):
    from apps.estimates.models import Estimate
    action = event.action.split(".", 1)[1]
    estimate = get(Estimate, event.data.get("estimate_id"))
    if estimate is None:
        return []
    actor, link = name_of(event.actor), url("estimate-version", project.pk, estimate.pk)
    title = f"Смета № {estimate.number}"
    if action == "submitted":
        return [Note(user, "approval", f"{title} ждёт согласования", f"{actor} отправил(а) смету на {money(estimate.total)}. Утвердите или отклоните её.", link)
                for user in members(project, CUSTOMER)]
    if action == "withdrawn":
        return [Note(user, "approval", f"{title} отозвана", f"{actor} забрал(а) смету на доработку.", link) for user in members(project, CUSTOMER)]
    if action in ("approved", "rejected"):
        users = [member_user(project, estimate.submitted_by_id)] if estimate.submitted_by_id else members(project, FOREMAN)
        if action == "approved":
            return [Note(user, "approval", f"{title} утверждена", f"{actor} утвердил(а) смету на {money(estimate.total)}.", link) for user in users]
        return [Note(user, "approval", f"{title} отклонена", f"{actor}: «{event.data.get('reason', '')}»", link) for user in users]
    return []


def change_rule(event, project):
    from apps.estimates.models import ChangeRequest
    action = event.action.split(".", 1)[1]
    change = get(ChangeRequest, event.data.get("change_id"))
    if change is None:
        return []
    actor, link = name_of(event.actor), url("estimate-change", project.pk, change.pk)
    title = f"Изменение сметы № {change.number}"
    if action == "submitted":
        return [Note(user, "approval", f"{title} ждёт согласования", f"{actor} предлагает изменение: {signed(event.data.get('delta'))}.", link)
                for user in members(project, CUSTOMER)]
    if action == "withdrawn":
        return [Note(user, "approval", f"{title} отозвано", f"{actor} отозвал(а) заявку.", link) for user in members(project, CUSTOMER)]
    author = member_user(project, change.created_by_id)
    if action == "approved":
        return [Note(author, "approval", f"{title} одобрено", f"{actor} одобрил(а) изменение. Новая смета: {money(event.data.get('total'))}.", link)]
    if action == "rejected":
        return [Note(author, "approval", f"{title} отклонено", f"{actor}: «{event.data.get('reason', '')}»", link)]
    return []


def expense_rule(event, project):
    from apps.finances.models import Expense
    action = event.action.split(".", 1)[1]
    expense = get(Expense, event.data.get("expense_id"))
    if expense is None:
        return []
    actor, link = name_of(event.actor), url("expense", project.pk, expense.pk)
    title = f"Расход № {expense.number}"
    # «Сохранить и отправить» creates the expense already pending: that is a submission too.
    if action == "submitted" or (action == "created" and event.data.get("status") == "pending"):
        return [Note(user, "approval", f"{title} ждёт подтверждения", f"{actor} добавил(а) расход на {money(expense.amount)}.", link)
                for user in members(project, CUSTOMER)]
    author = member_user(project, expense.created_by_id)
    if action == "confirmed":
        return [Note(author, "approval", f"{title} подтверждён", f"{actor} подтвердил(а) расход на {money(expense.amount)}.", link)]
    if action == "rejected":
        return [Note(author, "approval", f"{title} отклонён", f"{actor}: «{event.data.get('reason', '')}»", link)]
    return []


# ---------- Project and team ----------

def project_rule(event, project):
    actor = name_of(event.actor)
    if event.action == "project.started":
        return [Note(user, "project", f"Работы по проекту «{project.name}» начались", f"{actor} запустил(а) работы.", url("project", project.pk))
                for user in members(project, CUSTOMER, FOREMAN, EXECUTOR)]
    if event.action == "invitation.accepted":
        member = project.members.filter(pk=event.data.get("member_id")).first()
        role = member.get_role_display().lower() if member else "участник"
        return [Note(user, "project", f"{actor} присоединился(-ась) к проекту", f"Роль: {role}.", url("members", project.pk))
                for user in members(project, CUSTOMER, FOREMAN)]
    if event.action == "member.role_changed":
        member = project.members.filter(pk=event.data.get("member_id"), is_active=True).select_related("user").first()
        if member:
            return [Note(member.user, "project", f"Ваша роль в проекте «{project.name}» изменена",
                         f"{actor} назначил(а) вам роль «{member.get_role_display()}».", url("project", project.pk))]
    return []


RULES = {
    "task": task_rule,
    "remark": remark_rule,
    "stage": stage_rule,
    "estimate": estimate_rule,
    "estimate_change": change_rule,
    "expense": expense_rule,
    "project": project_rule,
    "invitation": project_rule,
    "member": project_rule,
}


def notes_for(event):
    """Notifications for one history event, without duplicates and without the author of the change."""
    rule = RULES.get(event.action.split(".", 1)[0])
    if rule is None:
        return []
    seen, result = set(), []
    for note in rule(event, event.project):
        if note.user is None or note.user.pk == event.actor_id or note.user.pk in seen:
            continue
        seen.add(note.user.pk)
        result.append(note)
    return result
