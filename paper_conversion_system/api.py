from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import signal
import string
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .models import new_job_id
from .job_store import copy_input, ensure_job_dirs, merge_job_meta, now_ts, read_job_meta, write_job_meta


ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Multipart form-data parser (replaces the deprecated `cgi` module)
# ---------------------------------------------------------------------------

def _parse_content_type(raw: str) -> tuple[str, dict[str, str]]:
    """Parse a Content-Type header value into (media_type, {param: value})."""
    if not raw:
        return "", {}
    parts = [p.strip() for p in raw.split(";")]
    ctype = parts[0].lower()
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            k, _, v = part.partition("=")
            params[k.strip().lower()] = v.strip().strip('"')
    return ctype, params


class _FormField:
    """One field parsed from a multipart/form-data body."""
    def __init__(self, data: bytes, filename: str | None = None) -> None:
        self.file = io.BytesIO(data)
        self.filename = filename
        self._data = data

    def getvalue(self) -> str:
        return self._data.decode("utf-8", errors="replace")


def _parse_multipart(rfile, content_length: int, boundary: str) -> dict[str, _FormField]:
    """Parse multipart/form-data and return {field_name: _FormField}."""
    if not boundary:
        return {}
    body = rfile.read(content_length)
    delim = ("--" + boundary).encode("ascii", errors="replace")
    fields: dict[str, _FormField] = {}
    for chunk in body.split(delim):
        chunk = chunk.lstrip(b"\r\n")
        if not chunk or chunk[:2] == b"--":
            continue
        sep = chunk.find(b"\r\n\r\n")
        if sep == -1:
            continue
        raw_headers = chunk[:sep].decode("utf-8", errors="replace")
        part_body = chunk[sep + 4:]
        if part_body.endswith(b"\r\n"):
            part_body = part_body[:-2]
        name: str | None = None
        filename: str | None = None
        for line in raw_headers.splitlines():
            if line.lower().startswith("content-disposition:"):
                cd = line.split(":", 1)[1].strip()
                for param in cd.split(";"):
                    param = param.strip()
                    if param.lower().startswith("name="):
                        name = param[5:].strip('"')
                    elif param.lower().startswith("filename="):
                        filename = param[9:].strip('"')
        if name is not None:
            fields[name] = _FormField(part_body, filename)
    return fields


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JOBS_ROOT = ROOT / "jobs"
UI_ROOT = ROOT / "ui"
BROWSE_HOME = Path.home()
DEFAULT_JOB_TIMEOUT_SECONDS = int(os.environ.get("PAPER_CONVERSION_JOB_TIMEOUT_SECONDS", "600"))
ACTIVE_JOB_STATUSES = {"queued", "running"}
TERMINAL_JOB_STATUSES = {"success", "failed"}


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
        """Override to log all messages to stdout."""
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

            raw_ctype = self.headers.get("Content-Type", "")
            print(f"[POST] Raw Content-Type: {raw_ctype}")
            ctype, pdict = _parse_content_type(raw_ctype)
            print(f"[POST] Parsed ctype: {ctype}")

            # Handle multipart file upload (secure)
            if ctype == "multipart/form-data":
                try:
                    content_length = int(self.headers.get('Content-Length', '0'))
                    form = _parse_multipart(self.rfile, content_length, pdict.get('boundary', ''))
                    print(f"[POST] Form keys: {list(form.keys())}")

                    # Check if file was uploaded
                    if "file" not in form:
                        return self._json({"error": "No file uploaded"}, 400)

                    file_item = form["file"]
                    if not file_item.filename:
                        return self._json({"error": "No file selected"}, 400)

                    # Extract formats from form
                    source_format = (form["source_format"].getvalue() if "source_format" in form else "").strip().lower()
                    target_format = (form["target_format"].getvalue() if "target_format" in form else "").strip().lower()

                    if not source_format or not target_format:
                        return self._json({"error": "Missing source_format or target_format"}, 400)

                    if source_format not in ["ieee", "acm"] or target_format not in ["ieee", "acm"]:
                        return self._json({"error": "Invalid format. Must be 'ieee' or 'acm'"}, 400)

                    # Secure filename: extract just the filename, no path traversal
                    original_filename = Path(file_item.filename).name

                    # Validate filename (no path traversal)
                    if ".." in original_filename or "/" in original_filename or "\\" in original_filename:
                        return self._json({"error": "Invalid filename"}, 400)

                    # Validate file extension
                    suffix = Path(original_filename).suffix.lower()
                    if suffix not in ['.pdf', '.tex', '.zip']:
                        return self._json({"error": f"Unsupported file type: {suffix}. Please upload .pdf, .tex, or .zip files."}, 400)

                    # Create isolated temp directory for this job
                    job_id = str(uuid.uuid4())
                    temp_base = Path(tempfile.gettempdir()) / "paper_conversion"
                    temp_base.mkdir(parents=True, exist_ok=True)
                    job_temp_dir = temp_base / job_id
                    job_temp_dir.mkdir(parents=True, exist_ok=True)

                    # Save uploaded file to temp directory
                    input_path = job_temp_dir / original_filename
                    with open(input_path, 'wb') as f:
                        f.write(file_item.file.read())

                    # Create job with temp file
                    meta = JOB_MANAGER.create_job(
                        source_format=source_format,
                        target_format=target_format,
                        input_path=str(input_path),
                        fidelity_mode="preserve",
                    )
                    return self._json(meta, 201)

                except Exception as e:
                    print(f"ERROR in file upload: {str(e)}")
                    traceback.print_exc()
                    return self._json({"error": f"File upload failed: {str(e)}"}, 500)

            # Legacy JSON support (for backwards compatibility, but less secure)
            elif ctype == "application/json":
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
                        if suffix not in ['.pdf', '.tex', '.bib', '.zip']:
                            return self._json({"error": f"Unsupported file type {suffix}. Please provide a PDF file, LaTeX project folder, or .zip archive."}, 400)

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

            else:
                return self._json({"error": "unsupported_content_type. Use multipart/form-data or application/json"}, 400)
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
