from django import forms
from apps.projects.models import ProjectMember
from apps.repairs.models import Stage


class ProjectForm(forms.Form):
    name = forms.CharField(label="Название проекта", max_length=200, widget=forms.TextInput(attrs={"placeholder": "Например, ремонт квартиры на Киевской"}))


class InvitationForm(forms.Form):
    email = forms.EmailField(label="Электронная почта", widget=forms.EmailInput(attrs={"placeholder": "name@example.com"}))
    role = forms.ChoiceField(label="Роль в проекте", choices=[("foreman", "Прораб"), ("executor", "Исполнитель")])


class MemberChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, member):
        return f"{member.user.get_full_name() or member.user.get_username()} — {member.get_role_display()}"


class StageForm(forms.Form):
    name = forms.CharField(label="Название этапа", max_length=200, widget=forms.TextInput(attrs={"placeholder": "Например, электрика"}))
    description = forms.CharField(label="Описание", required=False, widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Какие работы нужно выполнить?"}))
    start_date = forms.DateField(label="Дата начала", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    end_date = forms.DateField(label="Дата окончания", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
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
