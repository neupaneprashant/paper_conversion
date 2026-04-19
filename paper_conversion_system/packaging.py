from __future__ import annotations

from pathlib import Path
import shutil


def create_job_bundle(job_dir: Path) -> Path:
    bundle_base = job_dir / "artifacts" / "job_bundle"
    bundle_base.parent.mkdir(parents=True, exist_ok=True)
    zip_path = shutil.make_archive(str(bundle_base), 'zip', root_dir=job_dir)
    return Path(zip_path)
