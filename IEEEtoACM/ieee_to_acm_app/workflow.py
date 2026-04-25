from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from paper_conversion_system.models import CanonicalPaperRepresentation
from paper_conversion_system.orchestrator import route_and_run
from paper_conversion_system.pdf_ingest import parse_pdf_to_cpr

from .review import render_pdf_pages


@dataclass
class WorkflowEvent:
    name: str
    status: str
    detail: str
    outputs: list[str] = field(default_factory=list)


def run_reviewable_conversion(
    input_path: Path,
    workdir: Path,
    render_review: bool = True,
) -> dict[str, Any]:
    started_at = now_iso()
    workdir.mkdir(parents=True, exist_ok=True)
    preflight_dir = workdir / "preflight"
    review_dir = workdir / "review"
    input_pages_dir = review_dir / "input_pages"
    output_pages_dir = review_dir / "output_pages"
    preflight_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)

    events: list[WorkflowEvent] = []
    inventory = build_input_inventory(input_path)
    events.append(
        WorkflowEvent(
            name="inventory",
            status="done",
            detail=f"Collected {len(inventory)} input file(s).",
        )
    )

    cpr_summary: dict[str, Any] | None = None
    if input_path.is_file() and input_path.suffix.lower() == ".pdf":
        rendered_inputs: list[str] = []
        if render_review:
            rendered_inputs = render_pdf_pages(input_path, input_pages_dir)
        events.append(
            WorkflowEvent(
                name="render_input_pdf",
                status="done",
                detail=f"Rendered {len(rendered_inputs)} input page image(s).",
                outputs=rendered_inputs,
            )
        )

        cpr = parse_pdf_to_cpr(
            input_path,
            source_format_hint="ieee",
            assets_dir=preflight_dir / "figures",
        )
        cpr_summary = summarize_cpr(cpr)
        cpr_summary_path = preflight_dir / "cpr_summary.json"
        cpr_summary_path.write_text(json.dumps(cpr_summary, indent=2), encoding="utf-8")
        events.append(
            WorkflowEvent(
                name="preflight_pdf_summary",
                status="done",
                detail="Built a CPR summary from the PDF ingest path.",
                outputs=[str(cpr_summary_path)],
            )
        )

    result = route_and_run(
        source_format="ieee",
        target_format="acm",
        input_path=input_path,
        workdir=workdir,
    )
    result_dict = result.to_dict()
    events.append(
        WorkflowEvent(
            name="convert_ieee_to_acm",
            status=result.status,
            detail=f"Conversion finished with status '{result.status}'.",
            outputs=collect_core_outputs(workdir, result_dict),
        )
    )

    rendered_outputs: list[str] = []
    final_pdf = find_output_pdf(workdir, result)
    if final_pdf and render_review:
        rendered_outputs = render_pdf_pages(final_pdf, output_pages_dir)
        events.append(
            WorkflowEvent(
                name="render_output_pdf",
                status="done",
                detail=f"Rendered {len(rendered_outputs)} output page image(s).",
                outputs=rendered_outputs,
            )
        )

    workflow = {
        "started_at": started_at,
        "finished_at": now_iso(),
        "input_path": str(input_path),
        "workdir": str(workdir),
        "direction": "ieee_to_acm",
        "inventory": inventory,
        "cpr_summary": cpr_summary,
        "result": result_dict,
        "events": [asdict(event) for event in events],
        "manifest": [],
        "notes": [
            "This workflow log is an external audit trail, not hidden chain-of-thought.",
            "The conversion engine is the repo's existing paper_conversion_system pipeline.",
        ],
    }
    workflow_path = workdir / "workflow.json"
    workflow_path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")
    write_workflow_markdown(workflow, workdir / "workflow.md")
    workflow["manifest"] = build_manifest(workdir)
    workflow_path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")
    write_workflow_markdown(workflow, workdir / "workflow.md")
    return workflow


def build_input_inventory(input_path: Path) -> list[dict[str, Any]]:
    if input_path.is_file():
        return [inventory_item(input_path, input_path.parent)]

    items: list[dict[str, Any]] = []
    for child in sorted(input_path.rglob("*")):
        if child.is_file():
            items.append(inventory_item(child, input_path))
    return items


def inventory_item(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "relative_path": str(path.relative_to(root)) if path.is_relative_to(root) else path.name,
        "size_bytes": path.stat().st_size,
        "suffix": path.suffix.lower(),
    }


def summarize_cpr(cpr: CanonicalPaperRepresentation) -> dict[str, Any]:
    equation_artifacts = cpr.metadata.get("equation_artifacts", []) or []
    warnings = cpr.metadata.get("warnings", []) or []
    return {
        "title": cpr.title,
        "authors": cpr.authors,
        "abstract_present": bool(cpr.abstract.strip()),
        "keywords": cpr.keywords,
        "section_titles": [section.title for section in cpr.sections],
        "section_count": len(cpr.sections),
        "figure_count": len(cpr.figures),
        "table_count": len(cpr.tables),
        "equation_artifact_count": len(equation_artifacts),
        "reference_count": len(cpr.references),
        "ingest_confidence": cpr.metadata.get("ingest_confidence"),
        "warnings": warnings,
    }


def collect_core_outputs(workdir: Path, result_dict: dict[str, Any]) -> list[str]:
    outputs: list[str] = []
    for candidate in [
        workdir / "job_output.json",
        workdir / "final" / "main.tex",
        workdir / "final" / "main.pdf",
        workdir / "converted" / "main.tex",
    ]:
        if candidate.exists():
            outputs.append(str(candidate))
    converted_source = result_dict.get("converted_source_path")
    if converted_source and converted_source not in outputs:
        outputs.append(str(converted_source))
    final_pdf = result_dict.get("final_pdf_path")
    if final_pdf and final_pdf not in outputs:
        outputs.append(str(final_pdf))
    return outputs


def find_output_pdf(workdir: Path, result: Any) -> Path | None:
    candidate_paths = [
        Path(result.final_pdf_path) if getattr(result, "final_pdf_path", None) else None,
        workdir / "final" / "main.pdf",
        workdir / "converted" / "main.pdf",
    ]
    for candidate in candidate_paths:
        if candidate and candidate.exists():
            return candidate
    return None


def build_manifest(root: Path) -> list[str]:
    manifest: list[str] = []
    for child in sorted(root.rglob("*")):
        if child.is_file():
            manifest.append(str(child))
    return manifest


def write_workflow_markdown(workflow: dict[str, Any], output_path: Path) -> None:
    lines: list[str] = [
        "# IEEE to ACM workflow",
        "",
        "## 1. Input inventory",
    ]
    for item in workflow["inventory"]:
        lines.append(
            f"- `{item['relative_path']}` ({item['suffix'] or 'no suffix'}, {item['size_bytes']} bytes)"
        )

    cpr_summary = workflow.get("cpr_summary")
    if cpr_summary:
        lines.extend(
            [
                "",
                "## 2. Preflight PDF summary",
                f"- title: {cpr_summary['title']}",
                f"- authors: {', '.join(cpr_summary['authors']) if cpr_summary['authors'] else 'none detected'}",
                f"- sections: {cpr_summary['section_count']}",
                f"- figures: {cpr_summary['figure_count']}",
                f"- tables: {cpr_summary['table_count']}",
                f"- equation artifacts: {cpr_summary['equation_artifact_count']}",
                f"- references: {cpr_summary['reference_count']}",
                f"- ingest confidence: {cpr_summary['ingest_confidence']}",
            ]
        )

    result = workflow["result"]
    validation = result.get("validation", {})
    lines.extend(
        [
            "",
            "## 3. Conversion result",
            f"- job id: {result.get('job_id')}",
            f"- status: {result.get('status')}",
            f"- compile status: {validation.get('compile_status')}",
            f"- template compliance: {validation.get('template_compliance')}",
            f"- citation compliance: {validation.get('citation_compliance')}",
        ]
    )

    lines.extend(["", "## 4. Workflow events"])
    for event in workflow["events"]:
        lines.append(f"- {event['name']}: {event['status']} - {event['detail']}")

    lines.extend(["", "## 5. Output manifest"])
    for path in workflow["manifest"]:
        lines.append(f"- `{path}`")

    lines.extend(["", "## 6. Notes"])
    for note in workflow["notes"]:
        lines.append(f"- {note}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
