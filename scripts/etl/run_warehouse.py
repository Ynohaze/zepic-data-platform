"""Run the local dbt warehouse project with connection settings loaded from .env."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "warehouse"


def main():
    load_dotenv(ROOT / ".env")
    dbt = shutil.which("dbt", path=str(Path(sys.executable).parent)) or shutil.which("dbt")
    if dbt is None:
        raise SystemExit("dbt is not installed; run .venv/bin/python -m pip install -r requirements.txt")

    dbt_args = sys.argv[1:] or ["build"]
    command = [dbt, *dbt_args]
    if dbt_args != ["--version"]:
        command.extend([
            "--project-dir",
            str(PROJECT_DIR),
            "--profiles-dir",
            str(PROJECT_DIR),
        ])
    completed = subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=False)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
