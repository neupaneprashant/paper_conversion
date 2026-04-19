"""Regression runner for paper_conversion_system.

Runs the three pytest-style test modules under ``tests/`` without requiring
pytest itself (the environment here can't install pytest from PyPI right now),
and additionally runs the three required end-to-end regressions from
PROJECT_DELIVERABLE.md section 14:

    1. IEEE -> ACM conversion on samples/ieee_sample
    2. ACM -> IEEE conversion on samples/acm_sample
    3. PDF -> IEEE conversion on the sample PDF under ui/

Writes a regression summary to build/regressions/summary.json.
"""

from __future__ import annotations

import inspect
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from paper_conversion_system.orchestrator import route_and_run  # noqa: E402


def _collect_tests(module) -> list[tuple[str, Callable]]:
    tests: list[tuple[str, Callable]] = []
    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if name.startswith("test_"):
            tests.append((name, obj))
    return tests


def _run_module(mod_path: str) -> list[dict]:
    """Import a test module and run every test_* function with a tmp_path."""
    results: list[dict] = []
    mod = __import__(mod_path, fromlist=["*"])
    for name, fn in _collect_tests(mod):
        sig = inspect.signature(fn)
        tmp_dir: Path | None = None
        try:
            kwargs = {}
            if "tmp_path" in sig.parameters:
                tmp_dir = Path(tempfile.mkdtemp(prefix=f"pct_{name}_"))
                kwargs["tmp_path"] = tmp_dir
            fn(**kwargs)
            results.append({"test": f"{mod_path}::{name}", "status": "pass"})
        except AssertionError as exc:
            results.append({
                "test": f"{mod_path}::{name}",
                "status": "fail",
                "error": f"AssertionError: {exc}",
                "trace": traceback.format_exc(limit=4),
            })
        except Exception as exc:
            results.append({
                "test": f"{mod_path}::{name}",
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(limit=4),
            })
        finally:
            if tmp_dir is not None and tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
    return results


def _regression_latex(source_format: str, target_format: str, sample_dir: Path, workdir: Path) -> dict:
    sample_dir = sample_dir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        result = route_and_run(source_format, target_format, sample_dir, workdir)
        return {
            "regression": f"{source_format}->{target_format} (latex sample)",
            "job_id": result.job_id,
            "status": result.status,
            "direction": result.direction,
            "template_compliance": result.validation.template_compliance,
            "citation_compliance": result.validation.citation_compliance,
            "compile_status": result.validation.compile_status,
            "warnings": result.validation.warnings,
            "errors": result.validation.errors,
            "converted_source": result.converted_source_path,
            "final_pdf_path": result.final_pdf_path,
        }
    except Exception as exc:
        return {
            "regression": f"{source_format}->{target_format} (latex sample)",
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=4),
        }


def _regression_pdf(pdf_path: Path, source_format: str, target_format: str, workdir: Path) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        result = route_and_run(source_format, target_format, pdf_path, workdir)
        return {
            "regression": f"{source_format}->{target_format} (pdf ingest)",
            "pdf": str(pdf_path),
            "job_id": result.job_id,
            "status": result.status,
            "direction": result.direction,
            "template_compliance": result.validation.template_compliance,
            "citation_compliance": result.validation.citation_compliance,
            "compile_status": result.validation.compile_status,
            "warnings": result.validation.warnings[:10],
            "errors": result.validation.errors[:10],
            "converted_source": result.converted_source_path,
            "final_pdf_path": result.final_pdf_path,
        }
    except Exception as exc:
        return {
            "regression": f"{source_format}->{target_format} (pdf ingest)",
            "pdf": str(pdf_path),
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=4),
        }


def main() -> int:
    out_dir = ROOT / "build" / "regressions"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict = {"unit_tests": [], "regressions": []}

    for mod_path in [
        "tests.test_cpr_mapping",
        "tests.test_integration",
        "tests.test_regressions",
    ]:
        summary["unit_tests"].extend(_run_module(mod_path))

    samples = ROOT / "samples"
    summary["regressions"].append(
        _regression_latex("ieee", "acm", samples / "ieee_sample", out_dir / "ieee_to_acm_latex")
    )
    summary["regressions"].append(
        _regression_latex("acm", "ieee", samples / "acm_sample", out_dir / "acm_to_ieee_latex")
    )

    pdf_candidates = list((ROOT / "ui").glob("*.pdf"))
    if pdf_candidates:
        summary["regressions"].append(
            _regression_pdf(pdf_candidates[0], "ieee", "acm", out_dir / "pdf_to_acm")
        )

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    passed = sum(1 for r in summary["unit_tests"] if r["status"] == "pass")
    total = len(summary["unit_tests"])
    print(f"unit tests:    {passed}/{total} passed")
    for r in summary["unit_tests"]:
        if r["status"] != "pass":
            print(f"  [{r['status']}] {r['test']}: {r.get('error', '')}")

    for r in summary["regressions"]:
        print(
            f"regression:    {r.get('regression')}: status={r.get('status')} "
            f"template={r.get('template_compliance')} "
            f"compile={r.get('compile_status')}"
        )

    print(f"\nsummary written to {summary_path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
