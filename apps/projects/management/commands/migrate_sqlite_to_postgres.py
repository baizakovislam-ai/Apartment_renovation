"""Copy current application data and preserve a raw archive of the SQLite source."""
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.management import BaseCommand, CommandError, call_command
from django.core.management.color import no_style
from django.db import connections, transaction

from config.database import postgres_database


def add_database(alias, config):
    # ConnectionHandler normally supplies these defaults only at initial setup.
    config = {"ATOMIC_REQUESTS": False, "AUTOCOMMIT": True, "CONN_MAX_AGE": 0,
              "CONN_HEALTH_CHECKS": False, "OPTIONS": {}, "TIME_ZONE": None,
              "USER": "", "PASSWORD": "", "HOST": "", "PORT": "", "TEST": {}, **config}
    connections.databases[alias] = config
    return connections[alias]


def model_records(model, alias):
    data = serializers.serialize("json", model._base_manager.using(alias).order_by(model._meta.pk.name), use_natural_foreign_keys=True)
    return json.loads(data)


def archive_sqlite(snapshot, target, counts):
    def json_value(value):
        if isinstance(value, bytes):
            return {"__base64__": base64.b64encode(value).decode("ascii")}
        raise TypeError(type(value).__name__)

    with target.cursor() as cursor:
        cursor.execute("CREATE SCHEMA sqlite_archive")
        cursor.execute("CREATE TABLE sqlite_archive.source_tables (name text PRIMARY KEY, ddl text NOT NULL, columns jsonb NOT NULL)")
        cursor.execute("CREATE TABLE sqlite_archive.source_rows (table_name text NOT NULL REFERENCES sqlite_archive.source_tables(name), ordinal bigint NOT NULL, data jsonb NOT NULL, PRIMARY KEY(table_name,ordinal))")
        cursor.execute("CREATE TABLE sqlite_archive.import_info (id integer PRIMARY KEY, imported_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP, source_sha256 text NOT NULL, model_counts jsonb NOT NULL)")
        with sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True) as source:
            tables = source.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            for name, ddl in tables:
                quoted = '"' + name.replace('"', '""') + '"'
                rows = source.execute("SELECT * FROM " + quoted)
                columns = [field[0] for field in rows.description]
                cursor.execute("INSERT INTO sqlite_archive.source_tables VALUES (%s,%s,%s::jsonb)", [name, ddl, json.dumps(columns)])
                payload = [(name, ordinal, json.dumps(dict(zip(columns, row)), ensure_ascii=False, default=json_value)) for ordinal, row in enumerate(rows, 1)]
                if payload:
                    cursor.executemany("INSERT INTO sqlite_archive.source_rows VALUES (%s,%s,%s::jsonb)", payload)
        with snapshot.open("rb") as content:
            checksum = hashlib.file_digest(content, "sha256").hexdigest()
        cursor.execute("INSERT INTO sqlite_archive.import_info (id,source_sha256,model_counts) VALUES (1,%s,%s::jsonb)", [checksum, json.dumps(counts)])


class Command(BaseCommand):
    help = "Migrate an existing SQLite database into an empty PostgreSQL project database; never overwrite existing application records."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--source", default=str(settings.BASE_DIR / "db.sqlite3"))

    def handle(self, *args, **options):
        source_path = Path(options["source"]).resolve()
        if not source_path.is_file():
            raise CommandError("SQLite source file does not exist.")
        target_alias, source_alias = "postgres_transfer", "sqlite_transfer"
        target = add_database(target_alias, postgres_database())
        source = None
        try:
            target.ensure_connection()
            models = [model for model in apps.get_models() if model._meta.managed and not model._meta.proxy]
            ignored = {"auth.Permission", "contenttypes.ContentType"}
            existing_tables = set(target.introspection.table_names())
            if existing_tables and "django_migrations" not in existing_tables:
                raise CommandError("The PostgreSQL target is not an empty Django database. No data was overwritten.")
            for model in models:
                if model._meta.label not in ignored and model._meta.db_table in existing_tables and model._base_manager.using(target_alias).exists():
                    raise CommandError("The PostgreSQL target already contains application records. No data was overwritten.")
            with target.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname='sqlite_archive'")
                if cursor.fetchone():
                    raise CommandError("The SQLite import archive already exists. Re-import was refused.")
            directory = settings.BASE_DIR / "backups"
            directory.mkdir(exist_ok=True)
            snapshot = directory / ("sqlite_before_postgresql_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".sqlite3")
            with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as original, sqlite3.connect(snapshot) as backup:
                original.backup(backup)
            self.stdout.write(f"SQLite snapshot saved: {snapshot.name}")
            source = add_database(source_alias, {"ENGINE": "django.db.backends.sqlite3", "NAME": snapshot.as_uri() + "?mode=ro", "OPTIONS": {"uri": True}})
            # Validate source compatibility before changing the PostgreSQL schema.
            source_records = {model._meta.label: model_records(model, source_alias) for model in models if model._meta.label not in ignored}
            call_command("migrate", database=target_alias, interactive=False, verbosity=0)
            with TemporaryDirectory(prefix="repair-postgresql-import-") as temp:
                fixture = Path(temp) / "records.json"
                with fixture.open("w", encoding="utf-8") as output:
                    call_command("dumpdata", database=source_alias, exclude=["contenttypes", "auth.permission"], use_natural_foreign_keys=True, use_base_manager=True, stdout=output, verbosity=0)
                with transaction.atomic(using=target_alias):
                    call_command("loaddata", str(fixture), database=target_alias, verbosity=0)
                    counts = {}
                    for model in models:
                        if model._meta.label in ignored:
                            continue
                        label = model._meta.label
                        actual = model_records(model, target_alias)
                        if actual != source_records[label]:
                            raise CommandError(f"Verification failed for {label}. The data import was rolled back.")
                        counts[label] = len(actual)
                    with target.cursor() as cursor:
                        for statement in target.ops.sequence_reset_sql(no_style(), models):
                            cursor.execute(statement)
                    archive_sqlite(snapshot, target, counts)
            self.stdout.write(self.style.SUCCESS("PostgreSQL import completed; all application records and IDs verified."))
            self.stdout.write("Original SQLite data, including legacy tables/columns, is preserved in sqlite_archive and in the snapshot.")
            self.stdout.write(json.dumps(counts, ensure_ascii=False, sort_keys=True))
        finally:
            target.close()
            if source is not None:
                source.close()
