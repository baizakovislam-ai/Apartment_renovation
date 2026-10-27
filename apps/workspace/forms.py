import uuid
from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.estimates.models import CostType
from apps.projects.models import ProjectMember
from apps.repairs.models import Stage, Task

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
    email = forms.EmailField(label="Электронная почта", widget=forms.EmailInput(attrs={"placeholder": "name@example.com"}),
                             help_text="Человек должен войти (или зарегистрироваться) именно с этим email.")
    role = forms.ChoiceField(label="Роль в проекте", choices=[("foreman", "Прораб"), ("executor", "Исполнитель")])

    def __init__(self, *args, inviter_role="customer", **kwargs):
        super().__init__(*args, **kwargs)
        if inviter_role == "foreman":
            # The foreman builds the crew: executors only (ТЗ 4.1.1 «назначать исполнителей»).
            self.fields["role"].choices = [("executor", "Исполнитель")]


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class PhotosField(forms.FileField):
    """Several optional photos; the content check happens in the service (apps/repairs/images.py)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}))
        kwargs.setdefault("help_text", "JPEG, PNG или WebP, до 8 файлов по 8 МБ. Можно не прикладывать.")
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(item, initial) for item in data if item]
        return [single(data, initial)] if data else []


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
        self.task = task
        if task is None:
            self.fields["due_date"].widget.attrs["min"] = timezone.localdate().isoformat()

    def clean(self):
        data = super().clean()
        stage, due = data.get("stage"), data.get("due_date")
        if stage and due and not stage.start_date <= due <= stage.end_date:
            self.add_error("due_date", f"Срок должен быть в пределах этапа: {stage.start_date:%d.%m.%Y} — {stage.end_date:%d.%m.%Y}.")
        elif due and due < timezone.localdate() and (self.task is None or due != self.task.due_date):
            self.add_error("due_date", "Срок выполнения не может быть в прошлом.")
        return data


class TaskChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, task):
        return f"{task.stage.name} → {task.name}"


class RemarkForm(forms.Form):
    """New remark. The customer addresses the foreman or an executor, the foreman — an executor."""

    stage = StageChoiceField(label="Этап", queryset=Stage.objects.none(),
                             help_text="Только этапы, по которым идут работы или приёмка.")
    task = TaskChoiceField(label="Задача (необязательно)", queryset=Task.objects.none(), required=False, empty_label="— без привязки к задаче —")
    description = forms.CharField(label="Что не так", widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Опишите дефект и как должно быть"}))
    responsible = MemberChoiceField(label="Кто устраняет", queryset=ProjectMember.objects.none())
    due_date = forms.DateField(label="Срок устранения", widget=forms.DateInput(**DATE_WIDGET))
    images = PhotosField(label="Фото дефекта")
    request_id = forms.UUIDField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, project, author_role="customer", **kwargs):
        kwargs.setdefault("initial", {}).setdefault("request_id", uuid.uuid4())
        super().__init__(*args, **kwargs)
        stages = project.stages.filter(status__in=["in_progress", "completed", "pending_acceptance"])
        self.fields["stage"].queryset = stages
        self.fields["task"].queryset = Task.objects.filter(stage__in=stages).select_related("stage").order_by("stage__position", "id")
        responsible = project.members.filter(is_active=True).exclude(role="customer").select_related("user")
        if author_role == "foreman":
            responsible = responsible.filter(role="executor")
        self.fields["responsible"].queryset = responsible
        self.fields["due_date"].widget.attrs["min"] = timezone.localdate().isoformat()
        if not stages.exists():
            self.fields["stage"].help_text = "Сейчас нет этапов в работе. Запустите этап на странице «Этапы ремонта» — после этого к нему можно оставлять замечания."

    def clean(self):
        data = super().clean()
        stage, task = data.get("stage"), data.get("task")
        if stage and task and task.stage_id != stage.pk:
            self.add_error("task", "Задача должна относиться к выбранному этапу.")
        if data.get("due_date") and data["due_date"] < timezone.localdate():
            self.add_error("due_date", "Срок устранения не может быть в прошлом.")
        return data


class RemarkEditForm(forms.Form):
    description = forms.CharField(label="Что не так", widget=forms.Textarea(attrs={"rows": 4}))
    responsible = MemberChoiceField(label="Кто устраняет", queryset=ProjectMember.objects.none())
    due_date = forms.DateField(label="Срок устранения", widget=forms.DateInput(**DATE_WIDGET))
    version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, remark, author_role="customer", **kwargs):
        kwargs.setdefault("initial", {"description": remark.description, "responsible": remark.responsible,
                                      "due_date": remark.due_date, "version": remark.version})
        super().__init__(*args, **kwargs)
        responsible = remark.project.members.filter(is_active=True).exclude(role="customer").select_related("user")
        if author_role == "foreman":
            responsible = responsible.filter(role="executor")
        self.fields["responsible"].queryset = responsible


# ---------- Estimate and preliminary calculation (FR09, FR16) ----------

def unit_widget():
    return forms.TextInput(attrs={"list": "unit-options", "placeholder": "м², шт, м…", "autocomplete": "off"})


class EstimateLineForm(forms.Form):
    stage = StageChoiceField(label="Этап", queryset=Stage.objects.none())
    name = forms.CharField(label="Наименование", max_length=255, widget=forms.TextInput(attrs={"placeholder": "Например, укладка плитки на пол"}))
    cost_type = forms.ChoiceField(label="Вид затрат", choices=CostType.choices)
    unit = forms.CharField(label="Ед. изм.", max_length=20, widget=unit_widget())
    quantity = forms.DecimalField(label="Количество", min_value=Decimal("0.001"), max_digits=12, decimal_places=3,
                                  widget=forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"}))
    price = forms.DecimalField(label="Цена за ед., сом", min_value=0, max_digits=14, decimal_places=2,
                               widget=forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"}))
    revision = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, project, estimate, line=None, **kwargs):
        initial = kwargs.setdefault("initial", {})
        initial.setdefault("revision", estimate.revision)
        if line:
            for name in ["stage", "name", "cost_type", "unit", "quantity", "price"]:
                initial.setdefault(name, getattr(line, name))
        super().__init__(*args, **kwargs)
        self.fields["stage"].queryset = project.stages.exclude(status="cancelled")


class CalculationForm(forms.Form):
    name = forms.CharField(label="Название расчёта", max_length=200, widget=forms.TextInput(attrs={"placeholder": "Например, стандартный ремонт"}))
    note = forms.CharField(label="Заметка", required=False, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Откуда цены, что учтено"}))


class CalculationItemForm(forms.Form):
    work = forms.CharField(label="Работа или материал", max_length=255, widget=forms.TextInput(attrs={"placeholder": "Например, штукатурка стен"}))
    cost_type = forms.ChoiceField(label="Вид затрат", choices=CostType.choices)
    unit = forms.CharField(label="Ед. изм.", max_length=20, widget=unit_widget())
    volume = forms.DecimalField(label="Объём", min_value=Decimal("0.001"), max_digits=12, decimal_places=3,
                                widget=forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"}))
    price = forms.DecimalField(label="Цена за ед., сом", min_value=0, max_digits=14, decimal_places=2,
                               widget=forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"}))

    def __init__(self, *args, item=None, **kwargs):
        if item:
            kwargs.setdefault("initial", {name: getattr(item, name) for name in ["work", "cost_type", "unit", "volume", "price"]})
        super().__init__(*args, **kwargs)
