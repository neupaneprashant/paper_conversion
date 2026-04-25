from __future__ import annotations

from pathlib import Path
import time

from paper_conversion_system.api import JobManager
from paper_conversion_system.job_store import ensure_job_dirs, read_job_meta, write_job_meta
from paper_conversion_system.render import _remove_equation_residue


def _make_minimal_source(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.tex").write_text(
        r"""
\documentclass[conference]{IEEEtran}
\title{Runtime Sample}
\author{Alice}
\begin{document}
\maketitle
\begin{abstract}Abstract text\end{abstract}
\section{Introduction}
Hello world.
\end{document}
""",
        encoding="utf-8",
    )
    return root


def test_pdf_equation_cleanup_uses_bounded_windowing():
    paragraph = (
        "\\subsection{Scheme's operation procedure} "
        "In this section, the proposed scheme's operating is presented step-by-step as showing in values then send them back to the validation entity. "
        "Then the scanned RSSI values are collocated and sent to the validation entity. "
        "2) The validation entity will run the calculated RSSI values equation (1) to compute the distance between the client's devices and all access points in the location. "
        "PLlog = PL0 + 10gamma log10 d d0 (1) "
        "Where PLlog is Ptx -Prx. "
        "3) The previous step outcome will be run through equation (2) to estimate the user's device's position. "
        "(x -x1)2 + (y -y1)2 = d2 (x -x2)2 + (y -y2)2 = d2 (x -x3)2 + (y -y3)2 = d2 (2) "
        "4) After finding the user's Wi-Fi enabled devices x and y coordinates, the distance between the client's two devices will be estimated using equation (3). "
        "Distance = p (x2 -x1)2 + (y2 -y1)2 (3) "
        "5) The validation entity now determines if the user's is authorized to sign the email."
    )
    started = time.perf_counter()
    cleaned = _remove_equation_residue(paragraph)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0
    assert "PLlog = PL0 + 10gamma log10 d d0 (1)" not in cleaned
    assert "Distance = p (x2 -x1)2 + (y2 -y1)2 (3)" not in cleaned
    assert "The validation entity now determines" in cleaned


def test_job_manager_marks_stale_running_jobs_as_interrupted(tmp_path: Path):
    jobs_root = tmp_path / "jobs"
    job_dir = jobs_root / "job-stale"
    ensure_job_dirs(job_dir)
    write_job_meta(
        job_dir,
        {
            "job_id": "job-stale",
            "status": "running",
            "stage": "render",
            "source_format": "ieee",
            "target_format": "acm",
            "input_path": str(tmp_path / "input.pdf"),
            "created_at": 1.0,
            "updated_at": 1.0,
            "worker_pid": 999999,
            "timeout_seconds": 10,
            "error": None,
        },
    )

    manager = JobManager(
        jobs_root=jobs_root,
        workspace_root=tmp_path,
        worker_timeout_seconds=10,
        recover_on_init=True,
    )
    recovered = manager.get_job("job-stale")
    assert recovered["status"] == "failed"
    assert recovered["failure_kind"] == "interrupted"
    assert recovered["error"]["stage"] == "render"


def test_job_manager_times_out_worker_without_blocking_server(tmp_path: Path):
    jobs_root = tmp_path / "jobs"
    sample = _make_minimal_source(tmp_path / "sample")
    manager = JobManager(
        jobs_root=jobs_root,
        workspace_root=Path.cwd(),
        worker_timeout_seconds=1,
        worker_env_overrides={"PAPER_CONVERSION_DEBUG_SLEEP_SECONDS": "2"},
    )

    meta = manager.create_job("ieee", "acm", str(sample))
    assert meta["status"] == "running"
    assert meta["stage"] == "queued"
    assert meta["worker_pid"]

    deadline = time.time() + 8
    latest = meta
    while time.time() < deadline:
        health = manager.health()
        latest = manager.get_job(meta["job_id"])
        assert health["ok"] is True
        if latest["status"] == "failed":
            break
        time.sleep(0.2)

    assert latest["status"] == "failed"
    assert latest["failure_kind"] == "timeout"
    assert latest["error"]["stage"] in {"queued", "pdf_ingest", "normalize", "render", "compile", "package"}
