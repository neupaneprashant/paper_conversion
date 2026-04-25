from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import signal
import subprocess
import string
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import cgi
import json
import mimetypes
import traceback

from .models import new_job_id
from .job_store import copy_input, ensure_job_dirs, merge_job_meta, now_ts, read_job_meta, write_job_meta


ROOT = Path(__file__).resolve().parent.parent
JOBS_ROOT = ROOT / "jobs"
UI_ROOT = ROOT / "ui"
BROWSE_HOME = Path.home()
DEFAULT_JOB_TIMEOUT_SECONDS = int(os.environ.get("PAPER_CONVERSION_JOB_TIMEOUT_SECONDS", "180"))
ACTIVE_JOB_STATUSES = {"queued", "running"}
TERMINAL_JOB_STATUSES = {"success", "failed"}


def _default_browse_path() -> Path:
    for candidate in (BROWSE_HOME / "Desktop", BROWSE_HOME / "Downloads", BROWSE_HOME):
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    return ROOT.resolve()


def _iter_drive_roots() -> list[Path]:
    if os.name != "nt":
        return [Path("/")]
    drives: list[Path] = []
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        try:
            if drive.exists():
                drives.append(drive)
        except OSError:
            continue
    return drives


def _browse_shortcuts() -> list[dict]:
    shortcuts: list[dict] = []
    seen: set[str] = set()

    def add_shortcut(label: str, path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if not resolved.exists() or not resolved.is_dir():
            return
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        shortcuts.append({"label": label, "path": key})

    add_shortcut("Home", BROWSE_HOME)
    add_shortcut("Desktop", BROWSE_HOME / "Desktop")
    add_shortcut("Downloads", BROWSE_HOME / "Downloads")
    add_shortcut("Documents", BROWSE_HOME / "Documents")
    add_shortcut("Workspace", ROOT)

    for drive in _iter_drive_roots():
        label = drive.drive or str(drive)
        add_shortcut(label, drive)

    return shortcuts


def _resolve_browse_target(raw_path: str) -> Path:
    raw = (raw_path or "").strip().strip('"').strip("'")
    if not raw:
        return _default_browse_path()
    if re.fullmatch(r"[A-Za-z]:", raw):
        raw = raw + "\\"

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        target = candidate
    else:
        target = ROOT / candidate

    target = target.resolve()
    if target.exists() and target.is_file():
        target = target.parent
    return target


def _browse_items(target: Path) -> list[dict]:
    items: list[dict] = []
    for entry in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        try:
            is_dir = entry.is_dir()
            items.append({
                "name": entry.name,
                "path": str(entry.resolve()),
                "is_dir": is_dir,
                "is_file": entry.is_file(),
                "extension": entry.suffix.lower(),
            })
        except (PermissionError, OSError):
            continue
    return items


def _build_browse_payload(target: Path) -> dict:
    return {
        "current_path": str(target),
        "parent_path": str(target.parent) if target.parent != target else None,
        "items": _browse_items(target),
        "shortcuts": _browse_shortcuts(),
    }


class JobManager:
    """Local job manager with isolated worker processes and timeout recovery."""

    def __init__(
        self,
        jobs_root: Path | None = None,
        workspace_root: Path | None = None,
        worker_timeout_seconds: int | None = None,
        python_executable: str | None = None,
        worker_env_overrides: dict[str, str] | None = None,
        recover_on_init: bool = False,
    ) -> None:
        self.lock = threading.Lock()
        self.jobs_root = (jobs_root or JOBS_ROOT).resolve()
        self.workspace_root = (workspace_root or ROOT).resolve()
        self.worker_timeout_seconds = DEFAULT_JOB_TIMEOUT_SECONDS if worker_timeout_seconds is None else worker_timeout_seconds
        self.python_executable = python_executable or sys.executable
        self.worker_env_overrides = dict(worker_env_overrides or {})
        self._workers: dict[str, dict[str, object]] = {}
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        if recover_on_init:
            self.recover_stale_jobs()

    def create_job(self, source_format: str, target_format: str, input_path: str, fidelity_mode: str = "preserve") -> dict:
        self.reconcile_jobs()
        job_id = new_job_id()
        job_dir = self.jobs_root / job_id
        ensure_job_dirs(job_dir)
        copied = copy_input(Path(input_path), job_dir / "input")
        created_at = now_ts()
        meta = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "source_format": source_format,
            "target_format": target_format,
            "input_path": str(copied),
            "fidelity_mode": fidelity_mode,
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "updated_at": created_at,
            "timeout_seconds": self.worker_timeout_seconds,
            "worker_pid": None,
            "failure_kind": None,
            "error": None,
        }
        write_job_meta(job_dir, meta)
        return self._launch_worker(job_id)

    def get_job(self, job_id: str) -> dict:
        self.reconcile_jobs()
        return read_job_meta(self.jobs_root / job_id)

    def list_jobs(self) -> list[dict]:
        self.reconcile_jobs()
        if not self.jobs_root.exists():
            return []
        jobs: list[dict] = []
        for child in sorted(self.jobs_root.iterdir(), reverse=True):
            if not child.is_dir():
                continue
            meta = read_job_meta(child)
            if not meta or not meta.get("job_id"):
                continue
            jobs.append(meta)
        return jobs

    def health(self) -> dict:
        self.reconcile_jobs()
        with self.lock:
            active = sum(1 for info in self._workers.values() if info["process"].poll() is None)
        return {
            "ok": True,
            "active_worker_count": active,
            "jobs_root": str(self.jobs_root),
            "timestamp": now_ts(),
        }

    def reconcile_jobs(self) -> None:
        with self.lock:
            items = list(self._workers.items())
        for job_id, info in items:
            process: subprocess.Popen = info["process"]  # type: ignore[assignment]
            job_dir: Path = info["job_dir"]  # type: ignore[assignment]
            meta = read_job_meta(job_dir)
            if not meta:
                self._cleanup_worker(job_id)
                continue

            timeout_seconds = int(meta.get("timeout_seconds") or self.worker_timeout_seconds)
            started_at = float(meta.get("started_at") or meta.get("created_at") or now_ts())
            returncode = process.poll()
            if returncode is None:
                if timeout_seconds > 0 and (now_ts() - started_at) > timeout_seconds:
                    self._terminate_process(process)
                    finished_at = now_ts()
                    merge_job_meta(
                        job_dir,
                        {
                            "status": "failed",
                            "updated_at": finished_at,
                            "finished_at": finished_at,
                            "failure_kind": "timeout",
                            "error": {
                                "message": f"Job timed out after {timeout_seconds} seconds while in stage {meta.get('stage', 'unknown')}.",
                                "stage": meta.get("stage"),
                            },
                        },
                    )
                    self._cleanup_worker(job_id)
                continue

            if meta.get("status") in TERMINAL_JOB_STATUSES:
                self._cleanup_worker(job_id)
                continue

            finished_at = now_ts()
            merge_job_meta(
                job_dir,
                {
                    "status": "failed",
                    "updated_at": finished_at,
                    "finished_at": finished_at,
                    "failure_kind": "worker_exit",
                    "error": {
                        "message": f"Worker exited with code {returncode} before completing the job.",
                        "stage": meta.get("stage"),
                    },
                },
            )
            self._cleanup_worker(job_id)

    def _launch_worker(self, job_id: str) -> dict:
        job_dir = self.jobs_root / job_id
        stdout_path = job_dir / "artifacts" / "worker.stdout.log"
        stderr_path = job_dir / "artifacts" / "worker.stderr.log"
        stdout_handle = stdout_path.open("a", encoding="utf-8")
        stderr_handle = stderr_path.open("a", encoding="utf-8")
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.update(self.worker_env_overrides)
        started_at = now_ts()
        try:
            process = subprocess.Popen(
                [
                    self.python_executable,
                    "-m",
                    "paper_conversion_system.job_worker",
                    "--job-dir",
                    str(job_dir),
                ],
                cwd=self.workspace_root,
                stdout=stdout_handle,
                stderr=stderr_handle,
                env=env,
            )
        except Exception as exc:
            stdout_handle.close()
            stderr_handle.close()
            finished_at = now_ts()
            return merge_job_meta(
                job_dir,
                {
                    "status": "failed",
                    "updated_at": finished_at,
                    "finished_at": finished_at,
                    "failure_kind": "launch_error",
                    "error": {
                        "message": f"Worker launch failed: {exc}",
                    },
                },
            )

        meta = merge_job_meta(
            job_dir,
            {
                "status": "running",
                "stage": "queued",
                "worker_pid": process.pid,
                "started_at": started_at,
                "updated_at": started_at,
            },
        )
        with self.lock:
            self._workers[job_id] = {
                "process": process,
                "job_dir": job_dir,
                "stdout": stdout_handle,
                "stderr": stderr_handle,
            }
        return meta

    def recover_stale_jobs(self) -> None:
        if not self.jobs_root.exists():
            return
        for child in self.jobs_root.iterdir():
            if not child.is_dir():
                continue
            meta = read_job_meta(child)
            if not meta or meta.get("status") not in ACTIVE_JOB_STATUSES:
                continue
            worker_pid = meta.get("worker_pid")
            if isinstance(worker_pid, int) and worker_pid > 0:
                self._terminate_pid(worker_pid)
            finished_at = now_ts()
            merge_job_meta(
                child,
                {
                    "status": "failed",
                    "updated_at": finished_at,
                    "finished_at": finished_at,
                    "failure_kind": "interrupted",
                    "error": {
                        "message": "Job was interrupted by a server restart before completion.",
                        "stage": meta.get("stage"),
                    },
                },
            )

    def _cleanup_worker(self, job_id: str) -> None:
        with self.lock:
            info = self._workers.pop(job_id, None)
        if not info:
            return
        for key in ("stdout", "stderr"):
            handle = info.get(key)
            try:
                if handle is not None:
                    handle.close()
            except Exception:
                pass

    def _terminate_process(self, process: subprocess.Popen) -> None:
        try:
            process.terminate()
            process.wait(timeout=3)
            return
        except Exception:
            pass
        try:
            process.kill()
            process.wait(timeout=3)
        except Exception:
            pass

    def _terminate_pid(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
            return
        except Exception:
            pass
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                check=False,
            )


JOB_MANAGER = JobManager()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        """Override to log all messages to stdout"""
        print(f"[{self.client_address[0]}] {format % args}")
    
    def _json(self, data: dict | list, status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text: str, status: int = 200, ctype: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path

            static_map = {
                "/": (UI_ROOT / "index.html", "text/html; charset=utf-8"),
                "/index.html": (UI_ROOT / "index.html", "text/html; charset=utf-8"),
                "/job.html": (UI_ROOT / "job.html", "text/html; charset=utf-8"),
                "/styles.css": (UI_ROOT / "styles.css", "text/css; charset=utf-8"),
                "/app.js": (UI_ROOT / "app.js", "application/javascript; charset=utf-8"),
                "/job.js": (UI_ROOT / "job.js", "application/javascript; charset=utf-8"),
            }
            if path in static_map:
                file_path, ctype = static_map[path]
                if not file_path.exists():
                    return self._text(f"File not found: {file_path}", 404)
                return self._text(file_path.read_text(encoding="utf-8"), ctype=ctype)

            if path == "/api/health":
                return self._json(JOB_MANAGER.health())
            if path == "/api/jobs":
                return self._json(JOB_MANAGER.list_jobs())
            if path.startswith("/api/jobs/") and path.endswith("/download"):
                parts = path.strip("/").split("/")
                job_id = parts[2]
                data = JOB_MANAGER.get_job(job_id)
                bundle_path = data.get("bundle_path") if data else None
                if not bundle_path or not Path(bundle_path).exists():
                    return self._json({"error": "bundle_not_found"}, 404)
                file_path = Path(bundle_path)
                body = file_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/api/jobs/") and "/artifact/" in path:
                parts = path.strip("/").split("/")
                job_id = parts[2]
                artifact_name = parts[-1]
                job_dir = JOB_MANAGER.jobs_root / job_id
                candidate_paths = [
                    job_dir / "final" / artifact_name,
                    job_dir / "converted" / artifact_name,
                    job_dir / artifact_name,
                ]
                file_path = next((p for p in candidate_paths if p.exists() and p.is_file()), None)
                if not file_path:
                    return self._json({"error": "artifact_not_found"}, 404)
                body = file_path.read_bytes()
                ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/browse":
                # File browser API: list directory contents
                try:
                    raw_path = parse_qs(parsed.query).get("path", [""])[0]
                    target = _resolve_browse_target(raw_path)
                    if not target.exists():
                        return self._json({"error": "Path not found"}, 404)
                    if not target.is_dir():
                        return self._json({"error": "Path is not a directory"}, 400)

                    try:
                        payload = _build_browse_payload(target)
                    except (PermissionError, OSError) as e:
                        return self._json({"error": f"Cannot read directory: {str(e)}"}, 403)

                    return self._json(payload)
                except Exception as e:
                    return self._json({"error": f"Browse failed: {str(e)}"}, 400)
            
            if path.startswith("/api/jobs/"):
                job_id = path.split("/")[-1]
                data = JOB_MANAGER.get_job(job_id)
                if not data:
                    return self._json({"error": "job_not_found"}, 404)
                return self._json(data)
            return self._text("Not Found", 404)
        except Exception as e:
            print(f"ERROR in do_GET: {str(e)}")
            traceback.print_exc()
            return self._json({"error": f"Server error: {str(e)}"}, 500)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path != "/api/jobs":
                return self._text("Not Found", 404)

            ctype, pdict = cgi.parse_header(self.headers.get("Content-Type", ""))
            if ctype == "application/json":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    
                    # Validate and clean required fields
                    source_format = payload.get("source_format", "").strip().lower()
                    target_format = payload.get("target_format", "").strip().lower()
                    input_path = payload.get("input_path", "").strip()
                    fidelity_mode = payload.get("fidelity_mode", "preserve").strip().lower() or "preserve"
                    
                    # Strip quotes from path (from drag-drop or copy-paste)
                    if input_path.startswith('"') and input_path.endswith('"'):
                        input_path = input_path[1:-1]
                    if input_path.startswith("'") and input_path.endswith("'"):
                        input_path = input_path[1:-1]
                    
                    if not input_path or not source_format or not target_format:
                        return self._json({"error": "Missing required fields: input_path, source_format, target_format"}, 400)
                    
                    input_file = Path(input_path)
                    if not input_file.exists():
                        return self._json({"error": f"Input path not found: {input_path}"}, 404)
                    
                    # Accept both files and directories
                    if not (input_file.is_dir() or input_file.is_file()):
                        return self._json({"error": f"Input path must be a file or folder: {input_path}"}, 400)
                    
                    # Validate file type if it's a file
                    if input_file.is_file():
                        suffix = input_file.suffix.lower()
                        if suffix not in ['.pdf', '.tex', '.bib']:
                            return self._json({"error": f"Unsupported file type {suffix}. Please provide a PDF file or LaTeX project folder."}, 400)
                    
                    meta = JOB_MANAGER.create_job(
                        source_format=source_format,
                        target_format=target_format,
                        input_path=input_path,
                        fidelity_mode=fidelity_mode,
                    )
                    return self._json(meta, 201)
                except json.JSONDecodeError as e:
                    return self._json({"error": f"Invalid JSON: {str(e)}"}, 400)
                except Exception as e:
                    return self._json({"error": f"Job creation failed: {str(e)}"}, 500)

            return self._json({"error": "unsupported_content_type"}, 400)
        except Exception as e:
            print(f"ERROR in do_POST: {str(e)}")
            traceback.print_exc()
            return self._json({"error": f"Server error: {str(e)}"}, 500)


def run_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    JOB_MANAGER.recover_stale_jobs()
    JOB_MANAGER.reconcile_jobs()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Academic Paper Converter UI running on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
