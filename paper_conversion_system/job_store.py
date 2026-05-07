from __future__ import annotations

from pathlib import Path
import json
import shutil
import time
from typing import Any
import zipfile


# ---------------------------------------------------------------------------
# Zip extraction safety limits — enforced by ``_extract_zip_project`` to
# protect against zip-bomb uploads. These caps are deliberately generous for
# real LaTeX projects (which routinely include 30–80 MB of figure assets)
# but should refuse pathological archives that would expand to gigabytes of
# disk content from a few-MB upload.
# ---------------------------------------------------------------------------
_ZIP_TOTAL_UNCOMPRESSED_LIMIT = 200 * 1024 * 1024     # 200 MB cumulative
_ZIP_PER_ENTRY_UNCOMPRESSED_LIMIT = 60 * 1024 * 1024  # 60 MB per file
_ZIP_MAX_ENTRIES = 5_000                              # arbitrary safety net
_ZIP_MAX_COMPRESSION_RATIO = 200                      # reject > 200× ratio


class ZipExtractionError(ValueError):
    """Raised when a zip archive violates extraction safety limits.

    The HTTP layer should map this to a 4xx response so the user knows the
    archive was rejected before any disk space was wasted.
    """


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
    """Safely extract a uploaded LaTeX project archive.

    Enforces zip-bomb defences:
      * Per-entry uncompressed size cap (``_ZIP_PER_ENTRY_UNCOMPRESSED_LIMIT``)
      * Cumulative uncompressed size cap (``_ZIP_TOTAL_UNCOMPRESSED_LIMIT``)
      * Compression-ratio cap (``_ZIP_MAX_COMPRESSION_RATIO``) to refuse
        highly compressible payloads that would explode on disk
      * Maximum entry count (``_ZIP_MAX_ENTRIES``) to refuse archives with
        absurd numbers of files
      * Path-traversal guard (already present)
      * Streaming write with a running byte counter so a malicious entry
        whose declared size lies in the central directory still gets cut
        off mid-write rather than filling the disk
    """
    with zipfile.ZipFile(src) as archive:
        infolist = archive.infolist()
        members = [member for member in infolist if not member.is_dir()]
        if not members:
            raise FileNotFoundError(f"Zip archive has no files: {src}")

        # ── Pre-extraction safety checks ─────────────────────────────────
        if len(members) > _ZIP_MAX_ENTRIES:
            raise ZipExtractionError(
                f"Archive contains {len(members)} files (limit: {_ZIP_MAX_ENTRIES}). "
                "Refusing to extract."
            )

        total_uncompressed_declared = sum(max(0, m.file_size) for m in members)
        total_compressed = sum(max(0, m.compress_size) for m in members) or 1
        if total_uncompressed_declared > _ZIP_TOTAL_UNCOMPRESSED_LIMIT:
            raise ZipExtractionError(
                f"Archive declares {total_uncompressed_declared / (1024*1024):.1f} MB "
                f"of uncompressed content (limit: "
                f"{_ZIP_TOTAL_UNCOMPRESSED_LIMIT / (1024*1024):.0f} MB)."
            )
        ratio = total_uncompressed_declared / total_compressed
        if ratio > _ZIP_MAX_COMPRESSION_RATIO:
            raise ZipExtractionError(
                f"Archive compression ratio {ratio:.0f}× exceeds safety limit "
                f"of {_ZIP_MAX_COMPRESSION_RATIO}× — refusing extraction "
                "(possible zip-bomb)."
            )

        root_prefix = _common_root_prefix([member.filename for member in members])
        extracted_any = False
        bytes_written_total = 0
        for member in members:
            member_path = Path(member.filename)
            if any(part in {"", ".", ".."} for part in member_path.parts):
                continue
            if any(part.startswith("__MACOSX") for part in member_path.parts):
                continue
            if member.file_size > _ZIP_PER_ENTRY_UNCOMPRESSED_LIMIT:
                raise ZipExtractionError(
                    f"Archive entry '{member.filename}' is "
                    f"{member.file_size / (1024*1024):.1f} MB "
                    f"(per-file limit: {_ZIP_PER_ENTRY_UNCOMPRESSED_LIMIT / (1024*1024):.0f} MB)."
                )
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

            # Streaming copy with running budget so a member whose declared
            # ``file_size`` is a lie still gets cut off before exhausting
            # disk space.
            entry_budget = _ZIP_PER_ENTRY_UNCOMPRESSED_LIMIT
            chunk_size = 64 * 1024
            with archive.open(member) as source_handle, destination.open("wb") as dest_handle:
                while True:
                    chunk = source_handle.read(chunk_size)
                    if not chunk:
                        break
                    bytes_written_total += len(chunk)
                    entry_budget -= len(chunk)
                    if bytes_written_total > _ZIP_TOTAL_UNCOMPRESSED_LIMIT:
                        # Best-effort cleanup; the surrounding caller will
                        # also rmtree the destination tree on failure.
                        try:
                            dest_handle.close()
                            destination.unlink(missing_ok=True)
                        except Exception:
                            pass
                        raise ZipExtractionError(
                            f"Cumulative extracted size exceeded "
                            f"{_ZIP_TOTAL_UNCOMPRESSED_LIMIT / (1024*1024):.0f} MB "
                            "during extraction; refusing to continue."
                        )
                    if entry_budget < 0:
                        try:
                            dest_handle.close()
                            destination.unlink(missing_ok=True)
                        except Exception:
                            pass
                        raise ZipExtractionError(
                            f"Archive entry '{member.filename}' exceeded "
                            f"{_ZIP_PER_ENTRY_UNCOMPRESSED_LIMIT / (1024*1024):.0f} MB "
                            "while streaming."
                        )
                    dest_handle.write(chunk)
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
