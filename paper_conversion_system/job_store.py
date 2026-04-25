from __future__ import annotations

from pathlib import Path
import json
import shutil
import time
from typing import Any


def ensure_job_dirs(job_dir: Path) -> None:
    for name in ["input", "normalized", "converted", "final", "artifacts", "reports"]:
        (job_dir / name).mkdir(parents=True, exist_ok=True)


def write_job_meta(job_dir: Path, data: dict[str, Any]) -> None:
    (job_dir / "job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_job_meta(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "job.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def merge_job_meta(job_dir: Path, updates: dict[str, Any] | None = None) -> dict[str, Any]:
    data = read_job_meta(job_dir)
    if updates:
        data.update(updates)
    write_job_meta(job_dir, data)
    return data


def copy_input(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        target = dest_dir / src.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
        return target
    target = dest_dir / src.name
    shutil.copy2(src, target)
    return target


def now_ts() -> float:
    return time.time()
