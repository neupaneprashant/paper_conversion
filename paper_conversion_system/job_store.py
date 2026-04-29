from __future__ import annotations

from pathlib import Path
import json
import shutil
import time
from typing import Any
import zipfile


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
    if src.is_file() and src.suffix.lower() == ".zip":
        target = dest_dir / src.stem
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        _extract_zip_project(src, target)
        (target / ".paper_conversion_source_archive").write_text(src.name, encoding="utf-8")
        return target
    target = dest_dir / src.name
    shutil.copy2(src, target)
    return target


def now_ts() -> float:
    return time.time()


def _extract_zip_project(src: Path, dest_dir: Path) -> None:
    with zipfile.ZipFile(src) as archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        if not members:
            raise FileNotFoundError(f"Zip archive has no files: {src}")

        root_prefix = _common_root_prefix([member.filename for member in members])
        extracted_any = False
        for member in members:
            member_path = Path(member.filename)
            if any(part in {"", ".", ".."} for part in member_path.parts):
                continue
            if any(part.startswith("__MACOSX") for part in member_path.parts):
                continue
            relative = member_path
            if root_prefix:
                try:
                    relative = member_path.relative_to(root_prefix)
                except ValueError:
                    relative = member_path
            if not relative.parts:
                continue
            destination = (dest_dir / relative).resolve()
            if dest_dir.resolve() not in destination.parents and destination != dest_dir.resolve():
                raise ValueError(f"Unsafe zip entry path: {member.filename}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source_handle, destination.open("wb") as dest_handle:
                shutil.copyfileobj(source_handle, dest_handle)
            extracted_any = True

        if not extracted_any:
            raise FileNotFoundError(f"Zip archive did not contain extractable files: {src}")


def _common_root_prefix(names: list[str]) -> Path | None:
    normalized = [Path(name) for name in names if name and not name.startswith("/")]
    if not normalized:
        return None
    first_parts = normalized[0].parts
    if not first_parts:
        return None
    prefix: list[str] = []
    for index, part in enumerate(first_parts):
        if any(len(path.parts) <= index or path.parts[index] != part for path in normalized):
            break
        prefix.append(part)
    if not prefix:
        return None
    if len(prefix) == 1 and "." in prefix[0]:
        return None
    return Path(*prefix)
