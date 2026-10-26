from django.db import migrations


def number_stages(apps, schema_editor):
    Stage = apps.get_model("repairs", "Stage")
    database = schema_editor.connection.alias
    stages = Stage.objects.using(database)
    project_ids = stages.order_by().values_list("project_id", flat=True).distinct()
    for project_id in project_ids:
        ordered = list(stages.filter(project_id=project_id).order_by("position", "id"))
        for position, stage in enumerate(ordered, 1):
            stage.position = position
        stages.bulk_update(ordered, ["position"])


class Migration(migrations.Migration):
    dependencies = [("repairs", "0002_alter_stage_end_date_alter_stage_responsible_and_more")]
    operations = [migrations.RunPython(number_stages, migrations.RunPython.noop)]
