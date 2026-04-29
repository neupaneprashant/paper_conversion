from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol
import concurrent.futures
import json
import logging
import os
import re
import shutil
import time

from .agents import April, Friday
from .compiler import compile_project
from .job_store import copy_input
from .models import (
    CanonicalPaperRepresentation,
    JobOutput,
    StructuredLogEvent,
    StructuredLogger,
    fingerprint_path,
    new_job_id,
)
from .validators import validate_project
from .pdf_ingest import parse_pdf_to_cpr

if TYPE_CHECKING:
    from .openclaw import OpenClawConversionDriver

logger = logging.getLogger(__name__)


def _count_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text or ""))


def _latex_to_plaintext(latex: str) -> str:
    """Very lightweight LaTeX -> plain text approximation for fidelity scoring."""
    if not latex:
        return ""
    # Remove comments first to avoid counting commented-out text.
    cleaned = re.sub(r"(?m)^%.*$", " ", latex)
    cleaned = re.sub(r"(?m)(?<!\\)%.*$", " ", cleaned)
    # Drop common LaTeX commands while keeping their arguments (best-effort).
    cleaned = re.sub(r"\\[A-Za-z@]+(?:\*?)\s*(?:\[[^\]]*\])?", " ", cleaned)
    cleaned = cleaned.replace("{", " ").replace("}", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _extract_pdf_text(pdf_path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception:
        return ""
    doc = None
    try:
        doc = fitz.open(str(pdf_path))
        return "\n".join(page.get_text() for page in doc)
    except Exception:
        return ""
    finally:
        # PyMuPDF holds an OS file handle; closing avoids file-locking issues
        # on Windows and unbounded handle growth during repeated fidelity scoring.
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def _compute_fidelity(
    original_cpr: CanonicalPaperRepresentation,
    final_pdf_path: str | None,
    project_dir: Path,
    compile_report: dict | None = None,
) -> tuple[float | None, dict]:
    """Compute a post-compile fidelity score from text + compile signals.

    The score is intentionally cheap and explainable. It blends:
    - source/output word retention
    - figure preservation
    - citation resolution
    - layout warning pressure from hbox/vbox warnings
    """
    details: dict[str, object] = {}
    compile_signals = dict((compile_report or {}).get("fidelity_signals", {}) or {})
    details["compile_signals"] = compile_signals
    out_words = 0
    if final_pdf_path:
        out_text = _extract_pdf_text(Path(final_pdf_path))
        out_words = _count_words(out_text)
    details["output_words"] = out_words

    ingest_mode = str(original_cpr.metadata.get("ingest_mode", "") or "")
    source_words = 0
    if ingest_mode.startswith("pdf"):
        src_path = Path(str(original_cpr.metadata.get("source_path", "") or ""))
        src_text = _extract_pdf_text(src_path) if src_path.exists() else ""
        source_words = _count_words(src_text)
        details["source_kind"] = "pdf"
        details["source_path"] = str(src_path) if src_path else ""
    else:
        src_latex = str(original_cpr.metadata.get("source_latex_expanded", "") or "")
        source_words = _count_words(_latex_to_plaintext(src_latex))
        details["source_kind"] = "latex"

    details["source_words"] = source_words
    word_ratio = (out_words / source_words) if source_words else None
    if word_ratio is not None:
        details["word_ratio"] = round(word_ratio, 4)

    # Artifact ratios: conservative. For PDF ingest we try to preserve extracted
    # visual assets; for LaTeX inputs, rely on compilation + graphicspath.
    fig_in = len(getattr(original_cpr, "figures", []) or [])
    tab_in = len(getattr(original_cpr, "tables", []) or [])
    eq_in = len(list(original_cpr.metadata.get("equation_artifacts", []) or []))
    details["figures_in"] = fig_in
    details["tables_in"] = tab_in
    details["equations_in"] = eq_in

    # Count output figure assets by whether corresponding files exist.
    fig_out = 0
    for fig in getattr(original_cpr, "figures", []) or []:
        try:
            if (project_dir / fig.path).exists():
                fig_out += 1
        except Exception:
            continue
    details["figures_resolved"] = fig_out

    # Tables/equations are still approximated more loosely than figures.
    tab_out = tab_in if tab_in else 0
    eq_out = eq_in if eq_in else 0
    details["tables_resolved"] = tab_out
    details["equations_resolved"] = eq_out

    includegraphics_count = int(compile_signals.get("figure_include_count") or 0)
    missing_figure_count = int(compile_signals.get("missing_figure_count") or 0)
    undefined_citation_count = int(compile_signals.get("undefined_citation_count") or 0)
    citation_key_count = int(compile_signals.get("citation_key_count") or 0)
    overfull_hbox_count = int(compile_signals.get("overfull_hbox_count") or 0)
    underfull_hbox_count = int(compile_signals.get("underfull_hbox_count") or 0)
    overfull_vbox_count = int(compile_signals.get("overfull_vbox_count") or 0)
    underfull_vbox_count = int(compile_signals.get("underfull_vbox_count") or 0)

    figure_expected = max(fig_in, includegraphics_count)
    if figure_expected:
        figure_ratio = max(0.0, 1.0 - (missing_figure_count / figure_expected))
    elif fig_in:
        figure_ratio = fig_out / fig_in if fig_in else 1.0
    else:
        figure_ratio = 1.0
    details["figure_ratio"] = round(figure_ratio, 4)

    if citation_key_count:
        reference_ratio = max(0.0, 1.0 - (undefined_citation_count / citation_key_count))
    else:
        reference_ratio = 1.0
    details["reference_ratio"] = round(reference_ratio, 4)

    artifact_ratio = 1.0
    if fig_in:
        artifact_ratio *= (fig_out / fig_in)
    details["artifact_ratio"] = round(artifact_ratio, 4)

    layout_penalty = min(
        0.45,
        (0.03 * overfull_hbox_count)
        + (0.01 * underfull_hbox_count)
        + (0.04 * overfull_vbox_count)
        + (0.015 * underfull_vbox_count),
    )
    layout_ratio = max(0.0, 1.0 - layout_penalty)
    details["layout_ratio"] = round(layout_ratio, 4)
    details["layout_penalty"] = round(layout_penalty, 4)

    table_ratio = 1.0 if tab_in == 0 else (tab_out / tab_in if tab_in else 1.0)
    equation_ratio = 1.0 if eq_in == 0 else (eq_out / eq_in if eq_in else 1.0)
    details["table_ratio"] = round(table_ratio, 4)
    details["equation_ratio"] = round(equation_ratio, 4)

    if word_ratio is None:
        if not final_pdf_path:
            details["score_reason"] = "no_final_pdf"
            return 0.0, details
        return None, details

    word_ratio_capped = min(1.0, max(0.0, float(word_ratio)))
    score = (
        (0.45 * word_ratio_capped)
        + (0.20 * figure_ratio)
        + (0.20 * reference_ratio)
        + (0.10 * min(table_ratio, equation_ratio))
        + (0.05 * layout_ratio)
    )
    return round(score, 4), details


_OPENCLAW_LATEX_CHAR_LIMIT = 60_000
_OPENCLAW_LATEX_BYTE_LIMIT = 90_000
_OPENCLAW_INCLUDE_RE = re.compile(r"\\(?:input|include)\{([^}]+)\}")


class LLMContextProvider(Protocol):
    """Optional external-LLM hook for Comp's harmonization and repair stages.

    harmonize() is called after conversion for light cleanup.
    repair() is called when pdflatex fails, giving the LLM a chance to fix
    the broken LaTeX before the next compile attempt.
    """

    def harmonize(self, latex_source: str, task_brief: str) -> tuple[str, list[str]]:
        ...

    def repair(self, latex_source: str, error_log: str) -> tuple[str, list[str]]:
        ...


class Comp:
    """Final orchestration, harmonization, validation, and compile agent.

    Comp receives the converted project and source CPR, performs light
    harmonization, runs validation, attempts compilation, emits logs, and
    writes the final job output JSON summary.
    """

    name = "Comp"

    def __init__(self, llm_provider: LLMContextProvider | None = None) -> None:
        self.llm_provider = llm_provider

    def process(
        self,
        direction: str,
        converted_project: Path,
        original_cpr,
        workdir: Path,
        conversion_report: dict,
        job_id: str,
        conversion_method: str = "local",
        stage_callback: Callable[[str], None] | None = None,
    ) -> JobOutput:
        logger = StructuredLogger()
        logger.add(
            StructuredLogEvent(
                step="comp_input",
                ts=time.time(),
                input_fingerprint=fingerprint_path(converted_project),
                mapping_operations=["context_harmonization", "structural_validation", "compile_orchestration"],
            )
        )

        final_dir = workdir / "final"
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.copytree(converted_project, final_dir)

        harmonization_report = self._harmonize(final_dir)
        target_format = "acm" if direction == "ieee_to_acm" else "ieee"
        validation = validate_project(final_dir, original_cpr, target_format)
        if stage_callback is not None:
            stage_callback("compile")
        compile_status, artifacts, compile_report = compile_project(final_dir)
        validation.warnings.extend(harmonization_report.get("warnings", []))

        compile_blocked_by_tooling = any(
            "pdflatex not found" in snippet.lower()
            for snippet in compile_report.get("log_snippets", [])
        )

        # LLM repair loop â€” up to 2 attempts when compile fails and LLM is available.
        _MAX_LLM_REPAIRS = 2
        if (
            compile_status != "success"
            and not compile_blocked_by_tooling
            and self.llm_provider is not None
            and hasattr(self.llm_provider, "repair")
        ):
            for _repair_attempt in range(_MAX_LLM_REPAIRS):
                if compile_status == "success":
                    break
                error_text = _extract_compile_errors(final_dir, compile_report)
                if not error_text:
                    break
                try:
                    if stage_callback is not None:
                        stage_callback(f"repair_{_repair_attempt + 1}")
                    main_tex = final_dir / "main.tex"
                    broken_source = main_tex.read_text(encoding="utf-8", errors="ignore")
                    # Give the repair model a tiny bit of extra context beyond raw log lines.
                    context_bits = []
                    try:
                        context_bits.append(f"target_format={target_format}")
                        context_bits.append(f"ingest_mode={original_cpr.metadata.get('ingest_mode')}")
                        context_bits.append(f"figures={len(getattr(original_cpr, 'figures', []) or [])}")
                        context_bits.append(f"tables={len(getattr(original_cpr, 'tables', []) or [])}")
                        context_bits.append(f"equations={len(list(original_cpr.metadata.get('equation_artifacts', []) or []))}")
                    except Exception:
                        pass
                    extra_context = ("; ".join(context_bits)).strip()
                    repair_prompt = error_text if not extra_context else (error_text + "\n\n[context] " + extra_context)
                    repaired_source, repair_notes = self.llm_provider.repair(
                        broken_source, repair_prompt
                    )
                    if repaired_source and repaired_source.strip() != broken_source.strip():
                        main_tex.write_text(repaired_source, encoding="utf-8")
                        note_summary = "; ".join(repair_notes[:3]) if repair_notes else "no notes"
                        validation.warnings.append(
                            f"LLM repair attempt {_repair_attempt + 1}: {note_summary}"
                        )
                        compile_status, artifacts, compile_report = compile_project(final_dir)
                    else:
                        validation.warnings.append(
                            f"LLM repair attempt {_repair_attempt + 1}: no changes suggested"
                        )
                        break
                except Exception as _exc:
                    validation.warnings.append(
                        f"LLM repair attempt {_repair_attempt + 1} failed: {_exc}"
                    )
                    break

        validation.compile_status = compile_status
        if compile_blocked_by_tooling:
            validation.warnings.append(
                "PDF compile skipped: LaTeX tooling not installed (pdflatex/bibtex missing)."
            )
            validation.fidelity_score = None
            validation.fidelity_details = {
                "score_reason": "compile_blocked_by_tooling",
                "compile_signals": compile_report.get("fidelity_signals", {}),
            }
        elif compile_status != "success":
            validation.errors.append("Compile did not reach success threshold")
            validation.fidelity_score = 0.0
            validation.fidelity_details = {
                "score_reason": "compile_failed",
                "compile_signals": compile_report.get("fidelity_signals", {}),
            }
        else:
            fidelity_score, fidelity_details = _compute_fidelity(
                original_cpr,
                artifacts.pdf_path,
                final_dir,
                compile_report=compile_report,
            )
            validation.fidelity_score = fidelity_score
            validation.fidelity_details = fidelity_details or {}
            min_score = float(os.environ.get("PAPER_CONVERSION_FIDELITY_MIN_SCORE", "0.65") or "0.65")
            if fidelity_score is not None and fidelity_score < min_score:
                validation.errors.append(
                    f"Fidelity score below threshold ({fidelity_score} < {min_score}); output likely dropped content."
                )

        logger.add(
            StructuredLogEvent(
                step="comp_compile",
                ts=time.time(),
                input_fingerprint=fingerprint_path(final_dir),
                warnings=validation.warnings,
                errors=validation.errors,
                compile_command_sequence=compile_report.get("commands", []),
            )
        )
        logger.dump(workdir / "logs" / "trace.json")

        template_ok = validation.template_compliance in {"pass", "warn"}
        compile_ok = compile_status == "success" or compile_blocked_by_tooling
        status = "success" if template_ok and compile_ok else "failed"
        output = JobOutput(
            job_id=job_id,
            direction=direction,
            status=status,
            conversion_method=conversion_method,
            converted_source_path=str(converted_project),
            final_pdf_path=artifacts.pdf_path,
            validation=validation,
            reports={
                "conversion_report": conversion_report,
                "harmonization_report": harmonization_report,
                "compile_report": compile_report,
            },
        )
        report_path = workdir / "job_output.json"
        report_path.write_text(json.dumps(output.to_dict(), indent=2), encoding="utf-8")
        return output

    def _harmonize(self, project_dir: Path) -> dict:
        main = project_dir / "main.tex"
        warnings: list[str] = []
        operations: list[str] = []
        llm_notes: list[str] = []
        if main.exists():
            text = main.read_text(encoding="utf-8", errors="ignore")
            normalized = text.replace("\r\n", "\n")
            if normalized != text:
                operations.append("normalized_newlines")
            normalized2 = normalized.replace("\n\n\n", "\n\n")
            if normalized2 != normalized:
                operations.append("collapsed_excess_blank_lines")
            if "??" in normalized2:
                warnings.append("Found placeholder-like tokens '??' in output")
            if self.llm_provider is not None:
                try:
                    harmonized, notes = self.llm_provider.harmonize(
                        normalized2,
                        "Harmonize venue-specific voice without altering scientific claims.",
                    )
                    if harmonized and harmonized != normalized2:
                        normalized2 = harmonized
                        operations.append("llm_context_harmonization")
                    llm_notes.extend(notes)
                except Exception as exc:
                    warnings.append(f"LLM harmonization skipped: {exc}")
            main.write_text(normalized2, encoding="utf-8")
        return {
            "operations": operations or ["light_normalization_pass"],
            "warnings": warnings,
            "llm_notes": llm_notes,
        }


def route_and_run(
    source_format: str,
    target_format: str,
    input_path: Path,
    workdir: Path,
    job_id: str | None = None,
    llm_provider: LLMContextProvider | None = None,
    openclaw_driver: "OpenClawConversionDriver | None" = None,
    fidelity_mode: str = "preserve",
    stage_callback: Callable[[str], None] | None = None,
) -> JobOutput:
    direction = _route_direction(source_format, target_format)
    workdir.mkdir(parents=True, exist_ok=True)

    # Allow direct .zip inputs (CLI usage / tests). The HTTP JobManager path
    # already extracts zips, but callers may bypass it.
    if input_path.is_file() and input_path.suffix.lower() == ".zip":
        input_path = copy_input(input_path, workdir / "input")

    converted_dir = workdir / "converted"
    converted_dir.mkdir(parents=True, exist_ok=True)
    use_llm_postprocess = llm_provider is not None
    conversion_method = "local"
    fallback_reason = ""

    # PDF input feeds the CPR pipeline directly â€” no synthetic LaTeX detour.
    is_pdf = input_path.is_file() and input_path.suffix.lower() == ".pdf"
    agent_source: Path | CanonicalPaperRepresentation = input_path
    if is_pdf:
        if stage_callback is not None:
            stage_callback("pdf_ingest")
        agent_source = parse_pdf_to_cpr(
            input_path,
            source_format_hint=source_format,
            assets_dir=converted_dir / "figures",
            fidelity_mode=fidelity_mode,
        )

    # OpenClaw LLM path: use April/Friday as GPT-backed agent sessions.
    # Only applies to LaTeX source â€” PDF and archive inputs fall back to the local pipeline.
    is_archive_project = input_path.suffix.lower() == ".zip" or (
        input_path.is_dir() and (input_path / ".paper_conversion_source_archive").exists()
    )

    # Archive projects are already complete LaTeX projects; keep them on the
    # deterministic pipeline so large zip uploads do not stall in recomposition.
    if openclaw_driver is not None and not is_pdf and is_archive_project:
        openclaw_driver = None
        use_llm_postprocess = False
        fallback_reason = "archive_input_forces_local_pipeline"

    _llm_converted = False
    if openclaw_driver is not None and not is_pdf:
        if stage_callback is not None:
            stage_callback("parse")
        tex_file = _resolve_main_tex(input_path)
        if tex_file is None or not tex_file.exists():
            raise FileNotFoundError(f"No .tex file found in {input_path}")
        latex_source = _read_latex_with_includes(tex_file)

        if _can_send_to_openclaw(latex_source):
            if stage_callback is not None:
                stage_callback("recompose")
            # Run the LLM call in a thread so we can apply a hard timeout
            # without letting the job-manager process-kill fire first.
            # If it times out or errors, fall through to the local pipeline.
            _LLM_CALL_TIMEOUT = 480  # seconds â€” generous for large papers
            converted_latex: str | None = None
            conversion_report = None
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _executor:
                _future = _executor.submit(openclaw_driver.convert, latex_source, direction)
                try:
                    converted_latex, conversion_report = _future.result(timeout=_LLM_CALL_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    logger.warning("llm_conversion_timeout after %ss; falling back to local pipeline", _LLM_CALL_TIMEOUT)
                    openclaw_driver = None
                    use_llm_postprocess = False
                    fallback_reason = f"llm_timeout_{_LLM_CALL_TIMEOUT}s"
                except Exception as _llm_exc:
                    logger.warning("llm_conversion_failed (%s); falling back to local pipeline", _llm_exc)
                    openclaw_driver = None
                    use_llm_postprocess = False
                    fallback_reason = f"llm_error:{_llm_exc}"

            if converted_latex is not None:
                (converted_dir / "main.tex").write_text(converted_latex, encoding="utf-8")
                # Copy all project assets (figures, bib, style files) from source.
                src_root = input_path if input_path.is_dir() else input_path.parent
                for asset in src_root.rglob("*"):
                    if asset.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".eps", ".bib", ".cls", ".sty"):
                        dest = converted_dir / asset.relative_to(src_root)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(asset, dest)

                # Ensure \bibliography{references} always resolves â€” alias the first
                # .bib file found to references.bib if not already present.
                _ensure_references_bib(src_root, converted_dir)

                converted_project = converted_dir
                cpr = CanonicalPaperRepresentation()
                _llm_converted = True
                conversion_method = "llm"
        else:
            # Paper too large for LLM context â€” fall through to local pipeline.
            openclaw_driver = None
            use_llm_postprocess = False
            fallback_reason = "input_too_large_for_llm_context"

    if not _llm_converted:
        if direction == "ieee_to_acm":
            converted_project, conversion_report, cpr = April().convert(
                agent_source,
                converted_dir,
                stage_callback=stage_callback,
            )
        elif direction == "acm_to_ieee":
            converted_project, conversion_report, cpr = Friday().convert(
                agent_source,
                converted_dir,
                stage_callback=stage_callback,
            )
        else:
            raise ValueError(f"Unsupported direction: {source_format} -> {target_format}")
        conversion_method = "local"
        if fallback_reason:
            try:
                conversion_report.warnings.append(
                    f"LLM path unavailable; used local pipeline ({fallback_reason})."
                )
            except Exception:
                pass

    return Comp(llm_provider=llm_provider if use_llm_postprocess else None).process(
        direction,
        converted_project,
        cpr,
        workdir,
        conversion_report.__dict__,
        job_id=job_id or new_job_id(),
        conversion_method=conversion_method,
        stage_callback=stage_callback,
    )


def _extract_compile_errors(project_dir: Path, compile_report: dict) -> str:
    """Pull the most relevant error lines out of a failed pdflatex run.

    Prefers reading the actual main.log file (structured error lines starting
    with '!') so the repair agent gets precise line numbers and context.
    Falls back to the compile report's log_snippets if the log file is absent.
    """
    log_path = project_dir / "main.log"
    if log_path.exists():
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="ignore")
            lines = log_text.splitlines()
            extracted: list[str] = []
            for i, line in enumerate(lines):
                if line.startswith("!"):
                    extracted.extend(lines[i : i + 8])
                    extracted.append("---")
                    if len(extracted) >= 120:
                        break
            if extracted:
                return "\n".join(extracted)
        except Exception:
            pass
    snippets = compile_report.get("log_snippets", [])
    return "\n".join(snippets)


def _route_direction(source_format: str, target_format: str) -> str:
    source_format = source_format.lower()
    target_format = target_format.lower()
    if source_format == "ieee" and target_format == "acm":
        return "ieee_to_acm"
    if source_format == "acm" and target_format == "ieee":
        return "acm_to_ieee"
    raise ValueError(f"Unsupported conversion direction: {source_format} -> {target_format}")


def _resolve_main_tex(input_path: Path) -> Path | None:
    if input_path.is_file() and input_path.suffix.lower() == ".tex":
        return input_path
    if not input_path.is_dir():
        return None
    direct = next(input_path.glob("main.tex"), None)
    if direct is not None:
        return direct
    for candidate in input_path.glob("*.tex"):
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "\\begin{document}" in text:
            return candidate
    return next(input_path.rglob("*.tex"), None)


def _read_latex_with_includes(tex_path: Path, seen: set[Path] | None = None) -> str:
    resolved = tex_path.resolve()
    seen = seen or set()
    if resolved in seen:
        return ""
    seen.add(resolved)
    text = tex_path.read_text(encoding="utf-8", errors="ignore")

    def replace_include(match: re.Match[str]) -> str:
        raw_target = match.group(1).strip()
        if not raw_target:
            return ""
        candidate = tex_path.parent / raw_target
        if candidate.suffix.lower() != ".tex":
            candidate_with_ext = candidate.with_suffix(".tex")
            if candidate_with_ext.exists():
                candidate = candidate_with_ext
        if candidate.exists() and candidate.is_file():
            return _read_latex_with_includes(candidate, seen)
        return match.group(0)

    return _OPENCLAW_INCLUDE_RE.sub(replace_include, text)


def _can_send_to_openclaw(latex_source: str) -> bool:
    if not latex_source.strip():
        return False
    if len(latex_source) > _OPENCLAW_LATEX_CHAR_LIMIT:
        return False
    if len(latex_source.encode("utf-8", errors="ignore")) > _OPENCLAW_LATEX_BYTE_LIMIT:
        return False
    return True


def _ensure_references_bib(src_root: Path, output_dir: Path) -> None:
    """Guarantee that output_dir/references.bib contains real bibliography data.

    The LaTeX templates hardcode \\bibliography{references}, so bibtex always
    looks for references.bib.  If the source project uses a differently-named
    .bib file (paper.bib, main.bib, etc.) the lookup silently fails, producing
    an empty References section.

    Strategy:
    1. If references.bib already exists in output_dir and is non-empty, leave it.
    2. Otherwise find the largest .bib file in src_root and copy it as
       references.bib (in addition to its original name which was already copied).
    """
    ref_dest = output_dir / "references.bib"
    if ref_dest.exists() and ref_dest.stat().st_size > 50:
        return  # already has real content
    best: Path | None = None
    best_size = 0
    for bib in src_root.rglob("*.bib"):
        if not bib.is_file():
            continue
        sz = bib.stat().st_size
        if sz > best_size:
            best_size = sz
            best = bib
    if best is not None:
        shutil.copy2(best, ref_dest)

