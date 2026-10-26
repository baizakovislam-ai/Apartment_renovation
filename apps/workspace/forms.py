from django import forms
from apps.projects.models import ProjectMember, SavedContact


class ProjectForm(forms.Form):
    name = forms.CharField(label="Название проекта", max_length=200, widget=forms.TextInput(attrs={"placeholder": "Например, ремонт квартиры на Киевской"}))
    address = forms.CharField(label="Адрес квартиры", max_length=300, required=False)
    description = forms.CharField(label="Описание ремонта", required=False, widget=forms.Textarea(attrs={"rows": 3}))


class MemberForm(forms.Form):
    contact = forms.ModelChoiceField(label="Выбрать из ранее добавленных", queryset=SavedContact.objects.none(), required=False, empty_label="Новый человек — укажу email ниже")
    email = forms.EmailField(label="Или добавить по email", required=False, widget=forms.EmailInput(attrs={"placeholder": "name@example.com"}))
    role = forms.ChoiceField(label="Роль в проекте", choices=[("executor", "Исполнитель"), ("foreman", "Прораб")])

    def __init__(self, *args, owner, project, **kwargs):
        super().__init__(*args, **kwargs)
        emails = [email.lower() for email in project.members.values_list("user__email", flat=True)]
        self.fields["contact"].queryset = SavedContact.objects.filter(owner=owner).exclude(email__in=emails)

    def clean(self):
        data = super().clean()
        if bool(data.get("contact")) == bool(data.get("email")):
            raise forms.ValidationError("Выберите человека из списка или введите email — заполните один вариант.")
        return data


class QuickStageForm(forms.Form):
    name = forms.CharField(label="Название этапа", max_length=200, widget=forms.TextInput(attrs={"placeholder": "Например, демонтаж перегородок"}))


class MemberChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, member):
        return f"{member.user.get_full_name() or member.user.get_username()} — {member.get_role_display()}"


class StageForm(forms.Form):
    name = forms.CharField(label="Название этапа", max_length=200, widget=forms.TextInput(attrs={"placeholder": "Например, электрика"}))
    description = forms.CharField(label="Описание", required=False, widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Какие работы нужно выполнить?"}))
    start_date = forms.DateField(label="Дата начала", required=False, widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    end_date = forms.DateField(label="Дата окончания", required=False, widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    responsible = MemberChoiceField(label="Ответственный", required=False, queryset=ProjectMember.objects.none(), empty_label="Пока не назначен")

    def __init__(self, *args, project, stage=None, **kwargs):
        if stage and "initial" not in kwargs:
            kwargs["initial"] = {name: getattr(stage, name) for name in self.base_fields}
        super().__init__(*args, **kwargs)
        self.fields["responsible"].queryset = project.members.select_related("user")

    def clean(self):
        data = super().clean()
        if data.get("start_date") and data.get("end_date") and data["end_date"] < data["start_date"]:
            self.add_error("end_date", "Окончание не может быть раньше начала.")
        return data
