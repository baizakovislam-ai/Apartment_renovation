from uuid import uuid4

from django import forms

from apps.projects.models import ProjectMember
from apps.repairs.models import Stage
from .forms import MemberChoiceField


class OperationForm(forms.Form):
    request_id = forms.UUIDField(initial=uuid4, widget=forms.HiddenInput)


class TaskForm(OperationForm):
    name = forms.CharField(label="Название задачи", max_length=200)
    stage = forms.ModelChoiceField(label="Этап ремонта", queryset=Stage.objects.none(), empty_label="Выберите этап")
    description = forms.CharField(label="Что нужно сделать", required=False, widget=forms.Textarea(attrs={"rows": 4}))
    assignee = MemberChoiceField(label="Исполнитель", queryset=ProjectMember.objects.none(), empty_label="Выберите исполнителя")
    due_date = forms.DateField(label="Срок выполнения", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)

    def __init__(self, *args, project, task=None, **kwargs):
        initial = kwargs.pop("initial", {})
        if task:
            initial.update({key: getattr(task, key) for key in ["name", "stage", "description", "assignee", "due_date", "version"]})
            initial["request_id"] = task.request_id
        super().__init__(*args, initial=initial, **kwargs)
        self.fields["stage"].queryset = project.stages.exclude(status__in=["pending_acceptance", "accepted", "cancelled"])
        self.fields["assignee"].queryset = project.members.filter(role="executor").select_related("user")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data]
        if not 1 <= len(files) <= 8:
            raise forms.ValidationError("Добавьте от 1 до 8 фотографий.")
        return [super(MultipleFileField, self).clean(item, initial) for item in files]


class PhotoReportForm(OperationForm):
    description = forms.CharField(label="Описание выполненных работ", widget=forms.Textarea(attrs={"rows": 3}))
    images = MultipleFileField(label="Фотографии", help_text="JPEG, PNG или WebP. До 8 фотографий по 8 МБ; не больше 25 Мп каждая.", widget=MultipleFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}))


class TaskActionForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    action = forms.ChoiceField(choices=[(value, value) for value in ["start", "submit", "approve", "return", "delete"]])
    result = forms.CharField(required=False, max_length=10000)


class RemarkForm(OperationForm):
    stage = forms.ModelChoiceField(label="Этап ремонта", queryset=Stage.objects.none(), empty_label="Выберите этап")
    description = forms.CharField(label="Описание замечания", widget=forms.Textarea(attrs={"rows": 4}))
    responsible = MemberChoiceField(label="Ответственный за устранение", queryset=ProjectMember.objects.none(), empty_label="Выберите ответственного")
    due_date = forms.DateField(label="Устранить до", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stage"].queryset = project.stages.filter(status__in=["in_progress", "completed", "pending_acceptance"])
        self.fields["responsible"].queryset = project.members.filter(role__in=["foreman", "executor"]).select_related("user")


class RemarkActionForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    action = forms.ChoiceField(choices=[(value, value) for value in ["fix", "close", "reopen", "reply"]])
    text = forms.CharField(required=False, max_length=10000)


class AcceptanceForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    action = forms.ChoiceField(choices=[(value, value) for value in ["submit", "accept", "return"]])
    reason = forms.CharField(required=False, max_length=10000)
