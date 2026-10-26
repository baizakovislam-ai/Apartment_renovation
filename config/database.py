import os

from django.core.exceptions import ImproperlyConfigured


def postgres_database(environ=None):
    env = os.environ if environ is None else environ
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env.get("POSTGRES_DB", "repair_control"),
        "USER": env.get("POSTGRES_USER", "repair_control_app"),
        "PASSWORD": env.get("POSTGRES_PASSWORD", ""),
        "HOST": env.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": env.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": 5},
    }


def database_settings(base_dir, environ=None):
    env = os.environ if environ is None else environ
    engine = env.get("DB_ENGINE", "postgresql").lower()
    if engine in {"postgres", "postgresql"}:
        return {"default": postgres_database(env)}
    if engine == "sqlite":
        return {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": env.get("SQLITE_PATH") or base_dir / "db.sqlite3"}}
    raise ImproperlyConfigured("DB_ENGINE must be postgresql or sqlite.")
