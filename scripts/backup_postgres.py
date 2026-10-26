"""Save a portable pg_dump archive using local .env credentials."""
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
import sys

from dotenv import load_dotenv


def main():
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    binary = shutil.which("pg_dump")
    if not binary:
        candidate = Path(os.getenv("POSTGRES_BIN", r"C:\Program Files\PostgreSQL\17\bin")) / "pg_dump.exe"
        if candidate.is_file():
            binary = str(candidate)
    if not binary:
        print("pg_dump was not found. Set POSTGRES_BIN to the PostgreSQL bin directory.")
        return 1
    directory = root / "backups"
    directory.mkdir(exist_ok=True)
    output = directory / ("repair_control_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".dump")
    env = os.environ.copy()
    env.update(PGHOST=os.getenv("POSTGRES_HOST", "127.0.0.1"), PGPORT=os.getenv("POSTGRES_PORT", "5432"),
               PGUSER=os.getenv("POSTGRES_USER", "repair_control_app"), PGPASSWORD=os.getenv("POSTGRES_PASSWORD", ""),
               PGDATABASE=os.getenv("POSTGRES_DB", "repair_control"), PGCONNECT_TIMEOUT="5")
    result = subprocess.run([binary, "--format=custom", "--no-owner", "--no-acl", "--no-password", "--file", str(output)], env=env, capture_output=True, text=True)
    if result.returncode:
        output.unlink(missing_ok=True)
        print("Backup failed. Check the PostgreSQL connection and pg_dump version.")
        return result.returncode
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
