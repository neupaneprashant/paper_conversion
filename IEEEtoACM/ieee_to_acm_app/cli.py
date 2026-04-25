from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import platform
import shutil
import sys

import click

from .workflow import run_reviewable_conversion


@click.group()
def main() -> None:
    """Reviewable IEEE to ACM conversion wrapper."""


@main.command("convert")
@click.option("--input", "input_value", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--workdir", type=click.Path(path_type=Path), default=None)
@click.option("--no-review", is_flag=True, default=False)
def convert_command(input_value: Path, workdir: Path | None, no_review: bool) -> None:
    run_dir = workdir or default_run_dir()
    workflow = run_reviewable_conversion(
        input_path=input_value,
        workdir=run_dir,
        render_review=not no_review,
    )
    click.echo(json.dumps(workflow, indent=2))


@main.command("env-check")
def env_check_command() -> None:
    checks = {
        "python_executable": sys.executable,
        "python_version": sys.version,
        "platform": platform.platform(),
        "workspace_root": str(Path(__file__).resolve().parents[2]),
        "tools": {
            "pdflatex": shutil.which("pdflatex"),
            "bibtex": shutil.which("bibtex"),
            "tesseract": shutil.which("tesseract"),
        },
    }
    click.echo(json.dumps(checks, indent=2))


def default_run_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(__file__).resolve().parents[1] / "runs" / stamp


if __name__ == "__main__":
    main()
