import uuid
from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.projects.models import ProjectMember
from apps.repairs.models import Stage

DATE_WIDGET = {"format": "%Y-%m-%d", "attrs": {"type": "date"}}


class ProjectForm(forms.Form):
    name = forms.CharField(label="Название проекта", max_length=200, widget=forms.TextInput(attrs={"placeholder": "Например, ремонт квартиры на Киевской"}))
    address = forms.CharField(label="Адрес объекта", max_length=255, required=False, widget=forms.TextInput(attrs={"placeholder": "г. Бишкек, ул. Киевская, 100, кв. 12"}))
    area = forms.DecimalField(label="Площадь, м²", required=False, min_value=Decimal("0.01"), max_digits=8, decimal_places=2)
    start_date = forms.DateField(label="Начало ремонта", required=False, widget=forms.DateInput(**DATE_WIDGET))
    end_date = forms.DateField(label="Окончание ремонта", required=False, widget=forms.DateInput(**DATE_WIDGET))
    budget = forms.DecimalField(label="Лимит бюджета, сом", required=False, min_value=0, max_digits=14, decimal_places=2)
    description = forms.CharField(label="Описание", required=False, widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Что нужно отремонтировать, пожелания заказчика"}))

    def __init__(self, *args, project=None, **kwargs):
        if project is not None and "initial" not in kwargs:
            kwargs["initial"] = {name: getattr(project, name) for name in self.base_fields}
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        if data.get("start_date") and data.get("end_date") and data["end_date"] < data["start_date"]:
            self.add_error("end_date", "Окончание ремонта не может быть раньше начала.")
        return data


class InvitationForm(forms.Form):
    email = forms.EmailField(label="Электронная почта", widget=forms.EmailInput(attrs={"placeholder": "name@example.com"}))
    role = forms.ChoiceField(label="Роль в проекте", choices=[("foreman", "Прораб"), ("executor", "Исполнитель")])


class MemberChoiceField(forms.ModelChoiceField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("empty_label", "— выберите участника —")
        super().__init__(*args, **kwargs)

    def label_from_instance(self, member):
        return f"{member.user.get_full_name() or member.user.get_username()} — {member.get_role_display()}"


class StageChoiceField(forms.ModelChoiceField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("empty_label", "— выберите этап —")
        super().__init__(*args, **kwargs)

    def label_from_instance(self, stage):
        return f"{stage.name} ({stage.start_date:%d.%m} — {stage.end_date:%d.%m.%Y})"


class StageForm(forms.Form):
    name = forms.CharField(label="Название этапа", max_length=200, widget=forms.TextInput(attrs={"placeholder": "Например, электрика"}))
    description = forms.CharField(label="Описание", required=False, widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Какие работы нужно выполнить?"}))
    start_date = forms.DateField(label="Дата начала", widget=forms.DateInput(**DATE_WIDGET))
    end_date = forms.DateField(label="Дата окончания", widget=forms.DateInput(**DATE_WIDGET))
    responsible = MemberChoiceField(label="Ответственный", queryset=ProjectMember.objects.none())
    position = forms.IntegerField(label="Порядок в списке", min_value=0, initial=1)
    status = forms.ChoiceField(label="Статус")

    def __init__(self, *args, project, stage=None, **kwargs):
        if stage and "initial" not in kwargs:
            kwargs["initial"] = {name: getattr(stage, name) for name in self.base_fields}
        super().__init__(*args, **kwargs)
        self.fields["responsible"].queryset = project.members.select_related("user")
        allowed = {"planned": ["planned", "in_progress"], "in_progress": ["in_progress", "completed"], "completed": ["completed", "in_progress"]}
        statuses = allowed.get(stage.status, [stage.status]) if stage else ["planned"]
        self.fields["status"].choices = [(value, Stage.Status(value).label) for value in statuses]
        self.fields["status"].initial = stage.status if stage else "planned"

    def clean(self):
        data = super().clean()
        if data.get("start_date") and data.get("end_date") and data["end_date"] < data["start_date"]:
            self.add_error("end_date", "Окончание не может быть раньше начала.")
        return data


class TaskForm(forms.Form):
    """Hidden version and request_id protect against stale forms and double clicks (ТЗ 4.13)."""

    stage = StageChoiceField(label="Этап", queryset=Stage.objects.none())
    name = forms.CharField(label="Название задачи", max_length=200, widget=forms.TextInput(attrs={"placeholder": "Например, проложить кабель на кухне"}))
    description = forms.CharField(label="Описание", required=False, widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Что именно нужно сделать и как проверить результат"}))
    assignee = MemberChoiceField(label="Исполнитель", queryset=ProjectMember.objects.none())
    due_date = forms.DateField(label="Срок выполнения", widget=forms.DateInput(**DATE_WIDGET), help_text="Срок должен попадать в даты выбранного этапа.")
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    request_id = forms.UUIDField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, project, task=None, **kwargs):
        if task and "initial" not in kwargs:
            kwargs["initial"] = {name: getattr(task, name) for name in ["stage", "name", "description", "assignee", "due_date", "version"]}
        kwargs.setdefault("initial", {}).setdefault("request_id", uuid.uuid4())
        super().__init__(*args, **kwargs)
        stages = project.stages.exclude(status__in=["pending_acceptance", "accepted", "cancelled"])
        if task:
            stages = stages | project.stages.filter(pk=task.stage_id)
        self.fields["stage"].queryset = stages.distinct()
        self.fields["assignee"].queryset = project.members.filter(role="executor").select_related("user")
        if task and task.status != "todo":
            self.fields["assignee"].disabled = True
            self.fields["assignee"].help_text = "Исполнителя можно сменить только до начала выполнения."
        if not self.fields["assignee"].queryset.exists():
            self.fields["assignee"].help_text = "В проекте пока нет исполнителей — пригласите их на странице участников."

    def clean(self):
        data = super().clean()
        stage, due = data.get("stage"), data.get("due_date")
        if stage and due and not stage.start_date <= due <= stage.end_date:
            self.add_error("due_date", f"Срок должен быть в пределах этапа: {stage.start_date:%d.%m.%Y} — {stage.end_date:%d.%m.%Y}.")
        return data


class RemarkForm(forms.Form):
    stage = StageChoiceField(label="Этап", queryset=Stage.objects.none())
    description = forms.CharField(label="Описание замечания", widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Что не так и как должно быть"}))
    responsible = MemberChoiceField(label="Ответственный за устранение", queryset=ProjectMember.objects.none())
    due_date = forms.DateField(label="Срок устранения", widget=forms.DateInput(**DATE_WIDGET))
    request_id = forms.UUIDField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, project, **kwargs):
        kwargs.setdefault("initial", {}).setdefault("request_id", uuid.uuid4())
        super().__init__(*args, **kwargs)
        self.fields["stage"].queryset = project.stages.filter(status__in=["in_progress", "completed", "pending_acceptance"])
        self.fields["responsible"].queryset = project.members.exclude(role="customer").select_related("user")

    def clean_due_date(self):
        due = self.cleaned_data["due_date"]
        if due < timezone.localdate():
            raise forms.ValidationError("Срок устранения не может быть в прошлом.")
        return due
