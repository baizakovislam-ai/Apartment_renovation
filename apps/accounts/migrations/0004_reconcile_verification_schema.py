from django.db import migrations


def reconcile_schema(apps, schema_editor):
    """Repair databases that applied the other branch's version of migration 0002.

    Fresh databases already have the right schema. Preserve the old table as an
    archive so no existing verification data is discarded during reconciliation.
    """
    connection = schema_editor.connection
    model = apps.get_model("accounts", "EmailVerificationCode")
    table = model._meta.db_table
    with connection.cursor() as cursor:
        columns = {column.name for column in connection.introspection.get_table_description(cursor, table)}
    if "created_at" in columns:
        return
    if connection.vendor != "sqlite" or not {"sent_at", "nonce", "window_started_at"}.issubset(columns):
        raise RuntimeError("Unexpected email verification table schema; restore its matching migration history before proceeding.")
    archive = table + "_legacy_0002"
    if archive in connection.introspection.table_names():
        raise RuntimeError("A legacy verification archive already exists; inspect it before retrying.")
    quote = schema_editor.quote_name
    schema_editor.execute(f"ALTER TABLE {quote(table)} RENAME TO {quote(archive)}")
    schema_editor.create_model(model)
    schema_editor.execute(
        f"INSERT INTO {quote(table)} (id, user_id, code_hash, created_at, attempts) "
        f"SELECT id, user_id, code_hash, COALESCE(sent_at, window_started_at, CURRENT_TIMESTAMP), attempts "
        f"FROM {quote(archive)}"
    )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_passwordresetcode")]
    operations = [migrations.RunPython(reconcile_schema, migrations.RunPython.noop)]
