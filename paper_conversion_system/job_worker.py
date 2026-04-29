from __future__ import annotations

import argparse
import os
from pathlib import Path
import time
import traceback

from .job_store import merge_job_meta, now_ts, read_job_meta
from .orchestrator import route_and_run
from .packaging import create_job_bundle


def _load_openclaw():
    """Return (driver, provider) using stored OAuth tokens, or (None, None) if unavailable."""
    try:
        from .openclaw import load_config_from_disk, OpenClawConversionDriver, OpenClawLLMProvider
        config = load_config_from_disk()
        if config is None:
            return None, None
        return OpenClawConversionDriver(config), OpenClawLLMProvider(config)
    except Exception as exc:
        print(f"[OpenClaw] Not available, falling back to local pipeline: {exc}")
        return None, None


def _build_timeline(result: dict) -> list[dict]:
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


def _sleep_if_debug_enabled() -> None:
    raw = os.environ.get("PAPER_CONVERSION_DEBUG_SLEEP_SECONDS", "").strip()
    if not raw:
        return
    try:
        delay = max(0.0, float(raw))
    except ValueError:
        return
    if delay:
        time.sleep(delay)


def run_job(job_dir: Path) -> None:
    meta = read_job_meta(job_dir)
    if not meta:
        raise FileNotFoundError(f"Job metadata missing: {job_dir / 'job.json'}")

    merge_job_meta(
        job_dir,
        {
            "status": "running",
            "worker_pid": os.getpid(),
            "started_at": meta.get("started_at") or now_ts(),
            "updated_at": now_ts(),
            "error": None,
            "failure_kind": None,
        },
    )

    _sleep_if_debug_enabled()

    def stage_callback(stage: str) -> None:
        merge_job_meta(
            job_dir,
            {
                "status": "running",
                "stage": stage,
                "updated_at": now_ts(),
            },
        )

    openclaw_driver, openclaw_provider = _load_openclaw()
    if openclaw_driver:
        print(f"[OpenClaw] LLM conversion active (OAuth)")
    else:
        print(f"[OpenClaw] Using local pipeline (no OAuth tokens found)")

    try:
        result = route_and_run(
            source_format=meta["source_format"],
            target_format=meta["target_format"],
            input_path=Path(meta["input_path"]),
            workdir=job_dir,
            job_id=meta["job_id"],
            fidelity_mode=meta.get("fidelity_mode", "preserve"),
            stage_callback=stage_callback,
            openclaw_driver=openclaw_driver,
            llm_provider=openclaw_provider,
        )
        stage_callback("package")
        bundle = create_job_bundle(job_dir)
        finished_at = now_ts()
        failure_kind = None
        error = None
        if result.status != "success":
            compile_status = result.validation.compile_status
            if compile_status != "success":
                failure_kind = "compile_failed"
                error = {
                    "message": "Compile did not reach success threshold.",
                    "stage": "compile",
                }
            else:
                failure_kind = "conversion_failed"
                error = {
                    "message": "Conversion completed with validation failures.",
                    "stage": "completed",
                }
        merge_job_meta(
            job_dir,
            {
                "status": result.status,
                "stage": "completed",
                "updated_at": finished_at,
                "finished_at": finished_at,
                "result": result.to_dict(),
                "bundle_path": str(bundle),
                "timeline": _build_timeline(result.to_dict()),
                "failure_kind": failure_kind,
                "error": error,
            },
        )
    except Exception as exc:
        current = read_job_meta(job_dir)
        finished_at = now_ts()
        merge_job_meta(
            job_dir,
            {
                "status": "failed",
                "updated_at": finished_at,
                "finished_at": finished_at,
                "failure_kind": "exception",
                "error": {
                    "message": str(exc),
                    "stage": current.get("stage"),
                    "traceback": traceback.format_exc(),
                },
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one paper-conversion job in an isolated worker process.")
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args()
    run_job(Path(args.job_dir))


if __name__ == "__main__":
    main()
