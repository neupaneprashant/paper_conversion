from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import cgi
import json
import mimetypes
import threading
import traceback

from .models import new_job_id
from .job_store import ensure_job_dirs, write_job_meta, read_job_meta, copy_input, now_ts
from .orchestrator import route_and_run
from .packaging import create_job_bundle


ROOT = Path(__file__).resolve().parent.parent
JOBS_ROOT = ROOT / "jobs"
UI_ROOT = ROOT / "ui"


class JobManager:
    """Small local job manager for the web/API frontend.

    It persists job metadata under the jobs root, launches background conversion
    runs in threads, and exposes simple list/get semantics used by the HTTP API.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()

    def create_job(self, source_format: str, target_format: str, input_path: str) -> dict:
        job_id = new_job_id()
        job_dir = JOBS_ROOT / job_id
        ensure_job_dirs(job_dir)
        copied = copy_input(Path(input_path), job_dir / "input")
        meta = {
            "job_id": job_id,
            "status": "queued",
            "source_format": source_format,
            "target_format": target_format,
            "input_path": str(copied),
            "created_at": now_ts(),
            "updated_at": now_ts(),
            "error": None,
        }
        write_job_meta(job_dir, meta)
        threading.Thread(target=self._run_job, args=(job_id,), daemon=True).start()
        return meta

    def _run_job(self, job_id: str) -> None:
        job_dir = JOBS_ROOT / job_id
        meta = read_job_meta(job_dir)
        meta["status"] = "running"
        meta["updated_at"] = now_ts()
        write_job_meta(job_dir, meta)
        try:
            result = route_and_run(
                source_format=meta["source_format"],
                target_format=meta["target_format"],
                input_path=Path(meta["input_path"]),
                workdir=job_dir,
                job_id=job_id,
            )
            bundle = create_job_bundle(job_dir)
            meta["status"] = result.status
            meta["updated_at"] = now_ts()
            meta["result"] = result.to_dict()
            meta["bundle_path"] = str(bundle)
            meta["timeline"] = self._build_timeline(result.to_dict())
            write_job_meta(job_dir, meta)
        except Exception as exc:
            meta["status"] = "failed"
            meta["updated_at"] = now_ts()
            meta["error"] = {
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            write_job_meta(job_dir, meta)

    def get_job(self, job_id: str) -> dict:
        return read_job_meta(JOBS_ROOT / job_id)

    def _build_timeline(self, result: dict) -> list[dict]:
        compile_ok = result.get("validation", {}).get("compile_status") == "success"
        base = [
            {"agent": "April", "active_for": "ieee_to_acm", "status": "done" if result.get("direction") == "ieee_to_acm" else "idle"},
            {"agent": "Friday", "active_for": "acm_to_ieee", "status": "done" if result.get("direction") == "acm_to_ieee" else "idle"},
            {"agent": "Comp", "active_for": "all", "status": "done" if result.get("reports") else "idle"},
        ]
        for item in base:
            if item["agent"] == "Comp" and not compile_ok:
                item["status"] = "warning"
        return base

    def list_jobs(self) -> list[dict]:
        if not JOBS_ROOT.exists():
            return []
        jobs = []
        for child in sorted(JOBS_ROOT.iterdir(), reverse=True):
            if child.is_dir():
                meta = read_job_meta(child)
                # Ignore incomplete/placeholder job folders that have no metadata.
                if not meta or not meta.get("job_id"):
                    continue
                jobs.append(meta)
        return jobs


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
                job_dir = JOBS_ROOT / job_id
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
                    start_path = parse_qs(parsed.query).get("path", ["."])[0]
                    target = Path(start_path).resolve()
                    
                    # Security: prevent escape to parent/unsafe paths
                    # Allow any accessible directory
                    if not target.exists():
                        return self._json({"error": "Path not found"}, 404)
                    
                    items = []
                    try:
                        for entry in sorted(target.iterdir()):
                            try:
                                is_dir = entry.is_dir()
                                items.append({
                                    "name": entry.name,
                                    "path": str(entry.resolve()),
                                    "is_dir": is_dir,
                                    "is_file": entry.is_file(),
                                })
                            except (PermissionError, OSError):
                                continue
                    except (PermissionError, OSError) as e:
                        return self._json({"error": f"Cannot read directory: {str(e)}"}, 403)
                    
                    return self._json({
                        "current_path": str(target),
                        "parent_path": str(target.parent) if target.parent != target else None,
                        "items": items,
                    })
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
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Academic Paper Converter UI running on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
