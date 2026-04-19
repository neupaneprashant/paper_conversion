from __future__ import annotations

from pathlib import Path
from typing import Protocol
import json
import shutil
import time

from .agents import April, Friday
from .compiler import compile_project
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


class LLMContextProvider(Protocol):
    """Optional external-LLM hook for Comp's harmonization stage.

    Implementations receive the target LaTeX source and a short task brief,
    and return suggested replacement text plus a list of notes. This is the
    integration point for wiring Comp to an OAuth-authenticated ChatGPT
    (or similar) model without changing any other code path.
    """

    def harmonize(self, latex_source: str, task_brief: str) -> tuple[str, list[str]]:
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
        compile_status, artifacts, compile_report = compile_project(final_dir)
        validation.compile_status = compile_status
        validation.warnings.extend(harmonization_report.get("warnings", []))
        compile_blocked_by_tooling = any(
            "pdflatex not found" in snippet.lower()
            for snippet in compile_report.get("log_snippets", [])
        )
        if compile_blocked_by_tooling:
            validation.warnings.append(
                "PDF compile skipped: LaTeX tooling not installed (pdflatex/bibtex missing)."
            )
        elif compile_status != "success":
            validation.errors.append("Compile did not reach success threshold")

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
) -> JobOutput:
    direction = _route_direction(source_format, target_format)
    workdir.mkdir(parents=True, exist_ok=True)
    converted_dir = workdir / "converted"

    # PDF input feeds the CPR pipeline directly — no synthetic LaTeX detour.
    agent_source: Path | CanonicalPaperRepresentation = input_path
    if input_path.is_file() and input_path.suffix.lower() == ".pdf":
        agent_source = parse_pdf_to_cpr(input_path, source_format_hint=source_format)

    if direction == "ieee_to_acm":
        converted_project, conversion_report, cpr = April().convert(agent_source, converted_dir)
    elif direction == "acm_to_ieee":
        converted_project, conversion_report, cpr = Friday().convert(agent_source, converted_dir)
    else:
        raise ValueError(f"Unsupported direction: {source_format} -> {target_format}")

    return Comp(llm_provider=llm_provider).process(
        direction,
        converted_project,
        cpr,
        workdir,
        conversion_report.__dict__,
        job_id=job_id or new_job_id(),
    )


def _route_direction(source_format: str, target_format: str) -> str:
    source_format = source_format.lower()
    target_format = target_format.lower()
    if source_format == "ieee" and target_format == "acm":
        return "ieee_to_acm"
    if source_format == "acm" and target_format == "ieee":
        return "acm_to_ieee"
    raise ValueError(f"Unsupported conversion direction: {source_format} -> {target_format}")
