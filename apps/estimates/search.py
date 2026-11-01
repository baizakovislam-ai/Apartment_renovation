"""Search and filtering (ТЗ FR17): the approvals journal and project-wide search.

Both respect roles: the executor never sees estimates, calculations or other people's tasks.
"""
from datetime import date

from django.db.models import Q

from apps.projects.permissions import require_member
from apps.quality.models import StageDecision
from apps.quality.services import visible_remarks
from apps.repairs.task_services import visible_tasks
from .models import Calculation, Estimate, EstimateLine

APPROVAL_TYPES = [("estimate", "Смета"), ("change", "Изменение сметы"), ("acceptance", "Приёмка этапа")]
APPROVAL_STATUSES = [("pending", "Ждёт решения"), ("approved", "Одобрено"), ("rejected", "Отклонено / возвращено"), ("other", "Прочее")]
DECISION_STATUS = {"submit": "pending", "accept": "approved", "return": "rejected"}
ESTIMATE_STATUS = {"pending": "pending", "approved": "approved", "rejected": "rejected", "superseded": "other", "draft": "other", "withdrawn": "other"}


def parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def approval_items(project, user, params):
    """One list of everything that went through approval, newest first, with filters q/type/status/author/date_from/date_to."""
    member = require_member(project, user)
    if member.role == "executor":
        return []
    items = []
    for estimate in project.estimates.exclude(submitted_at__isnull=True).select_related("submitted_by", "decided_by"):
        items.append({
            "type": "estimate", "type_label": "Смета", "title": f"Смета, версия {estimate.number}", "object_id": estimate.pk,
            "status": ESTIMATE_STATUS[estimate.status], "status_label": estimate.get_status_display(), "raw_status": estimate.status,
            "amount": estimate.total, "author": estimate.submitted_by, "created_at": estimate.submitted_at,
            "decided_by": estimate.decided_by, "decided_at": estimate.decided_at,
            "text": estimate.decision_reason or estimate.comment,
        })
    changes = project.change_requests.exclude(submitted_at__isnull=True).select_related("submitted_by", "decided_by").prefetch_related("items__line")
    for change in changes:
        items.append({
            "type": "change", "type_label": "Изменение сметы", "title": f"Изменение сметы №{change.number}", "object_id": change.pk,
            "status": ESTIMATE_STATUS[change.status], "status_label": change.get_status_display(), "raw_status": change.status,
            "amount": change.cost_delta, "author": change.submitted_by, "created_at": change.submitted_at,
            "decided_by": change.decided_by, "decided_at": change.decided_at,
            "text": change.decision_reason or change.reason,
        })
    decisions = list(StageDecision.objects.filter(project=project).select_related("stage", "actor"))
    latest = {}
    for decision in decisions:  # newest first: the first one seen per stage is the latest
        latest.setdefault(decision.stage_id, decision.pk)
    for decision in decisions:
        status = DECISION_STATUS[decision.action]
        if decision.action == "submit" and not (latest[decision.stage_id] == decision.pk and decision.stage.status == "pending_acceptance"):
            status = "other"  # this hand-over has already been decided
        items.append({
            "type": "acceptance", "type_label": "Приёмка этапа", "title": f"Этап «{decision.stage.name}»", "object_id": decision.stage_id,
            "status": status, "status_label": decision.get_action_display(), "raw_status": decision.action,
            "amount": None, "author": decision.actor, "created_at": decision.created_at, "decided_by": None, "decided_at": None,
            "text": decision.reason,
        })
    query = (params.get("q") or "").strip().lower()
    if query:
        items = [item for item in items if query in f"{item['title']} {item['text']} {item['author'].get_full_name()} {item['author'].email}".lower()]
    if params.get("type") in dict(APPROVAL_TYPES):
        items = [item for item in items if item["type"] == params["type"]]
    if params.get("status") in dict(APPROVAL_STATUSES):
        items = [item for item in items if item["status"] == params["status"]]
    if str(params.get("author") or "").isdigit():
        items = [item for item in items if item["author"].pk == int(params["author"])]
    date_from, date_to = parse_date(params.get("date_from")), parse_date(params.get("date_to"))
    if date_from:
        items = [item for item in items if item["created_at"].date() >= date_from]
    if date_to:
        items = [item for item in items if item["created_at"].date() <= date_to]
    return sorted(items, key=lambda item: item["created_at"], reverse=True)


RUSSIAN_ENDINGS = ("иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "иях", "ах", "ях", "ой", "ей", "ий", "ый", "ая", "яя",
                   "ое", "ее", "ом", "ем", "ам", "ям", "ов", "ев", "ы", "и", "а", "я", "о", "е", "у", "ю", "ь", "й")


def search_terms(query):
    """Words of the query reduced to a simple stem, so «кабель» also finds «кабеля», «плитка» — «плитки»."""
    terms = []
    for word in (query or "").lower().split():
        word = word.strip(".,;:!?«»\"'()")
        if len(word) < 2:
            continue
        if len(word) >= 5:
            for ending in RUSSIAN_ENDINGS:
                if word.endswith(ending) and len(word) - len(ending) >= 4:
                    word = word[: -len(ending)]
                    break
        terms.append(word)
    return terms


def match_all(queryset, terms, fields):
    """Every term must be found in at least one of the fields."""
    for term in terms:
        condition = Q()
        for field in fields:
            condition |= Q(**{f"{field}__icontains": term})
        queryset = queryset.filter(condition)
    return queryset.distinct()


def project_search(project, user, query):
    """Find words across the project: tasks, stages, remarks, estimate lines, calculations, people."""
    member = require_member(project, user)
    terms = search_terms(query)
    if not terms:
        return None
    manager = member.role in ("customer", "foreman")
    results = {
        "tasks": list(match_all(visible_tasks(project, user), terms, ["name", "description", "result"])[:20]),
        "stages": list(match_all(project.stages.all(), terms, ["name", "description"])[:20]),
        "remarks": list(match_all(visible_remarks(project, user), terms, ["description", "comments__text"])[:20]),
        "members": list(match_all(project.members.filter(is_active=True).select_related("user"), terms, ["user__full_name", "user__email"])[:20]),
        "estimate_lines": [], "calculations": [],
    }
    if manager:
        lines = EstimateLine.objects.filter(estimate__project=project).exclude(estimate__status=Estimate.Status.SUPERSEDED).select_related("estimate", "stage")
        results["estimate_lines"] = list(match_all(lines, terms, ["name", "stage__name"])[:20])
        results["calculations"] = list(match_all(Calculation.objects.filter(project=project), terms, ["name", "note", "items__work"])[:20])
    results["count"] = sum(len(value) for value in results.values())
    results["terms"] = terms
    return results
