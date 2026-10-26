from django.conf import settings
from django.db import migrations


def remember_existing_team(apps, schema_editor):
    Contact = apps.get_model("projects", "SavedContact")
    Member = apps.get_model("projects", "ProjectMember")
    Invitation = apps.get_model("projects", "ProjectInvitation")
    Event = apps.get_model("projects", "ProjectEvent")
    User = apps.get_model(settings.AUTH_USER_MODEL)
    database = schema_editor.connection.alias

    def remember(owner_id, email, name=""):
        if email:
            contact, _ = Contact.objects.using(database).get_or_create(owner_id=owner_id, email=email.strip().lower())
            if name:
                contact.name = name
                contact.save(using=database, update_fields=["name"])

    for member in Member.objects.using(database).exclude(role="customer").select_related("project", "user").iterator():
        remember(member.project.created_by_id, member.user.email, member.user.full_name)
    for invitation in Invitation.objects.using(database).all().iterator():
        remember(invitation.invited_by_id, invitation.email)
    for event in Event.objects.using(database).filter(action="member.removed").iterator():
        user = User.objects.using(database).filter(pk=event.data.get("user_id")).first()
        if user:
            remember(event.actor_id, user.email, user.full_name)


class Migration(migrations.Migration):
    dependencies = [("projects", "0002_project_address_project_description_savedcontact")]
    operations = [migrations.RunPython(remember_existing_team, migrations.RunPython.noop)]
