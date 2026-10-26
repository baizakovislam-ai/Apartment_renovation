"""Create the project's PostgreSQL role/database without changing existing roles."""
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
import psycopg
from psycopg import sql


def main():
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    app_user = os.getenv("POSTGRES_USER", "repair_control_app")
    app_password = os.getenv("POSTGRES_PASSWORD", "")
    database = os.getenv("POSTGRES_DB", "repair_control")
    admin_password = os.getenv("POSTGRES_ADMIN_PASSWORD", "")
    if not app_password or not admin_password:
        print("Fill POSTGRES_PASSWORD and POSTGRES_ADMIN_PASSWORD in .env first.")
        return 1
    common = {"host": os.getenv("POSTGRES_HOST", "127.0.0.1"), "port": os.getenv("POSTGRES_PORT", "5432"), "connect_timeout": 5}
    try:
        with psycopg.connect(**common, dbname="postgres", user=os.getenv("POSTGRES_ADMIN_USER", "postgres"), password=admin_password, autocommit=True) as admin:
            if admin.info.server_version < 160000:
                print("This Django installation requires PostgreSQL 16 or newer.")
                return 1
            role_exists = admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", [app_user]).fetchone()
            if role_exists:
                # Validate supplied credentials; never reset another project's role.
                with psycopg.connect(**common, dbname="postgres", user=app_user, password=app_password):
                    pass
            else:
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEROLE CREATEDB PASSWORD {}").format(sql.Identifier(app_user), sql.Literal(app_password)))
                print("Project login role created.")
            existing = admin.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", [database]).fetchone()
            if existing:
                if existing[0] != app_user:
                    print("Database already belongs to another role; no existing database was changed.")
                    return 1
                print("Existing project database retained.")
            else:
                admin.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(sql.Identifier(database), sql.Identifier(app_user)))
                print("Project database created.")
        with psycopg.connect(**common, dbname=database, user=app_user, password=app_password) as connection:
            connection.execute("SELECT 1")
        print("PostgreSQL connection ready. No application records were created.")
        return 0
    except psycopg.Error as error:
        print(f"PostgreSQL setup failed: {type(error).__name__} (SQLSTATE {error.sqlstate or 'connection error'}). Check the credentials in .env.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
