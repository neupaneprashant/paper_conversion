from __future__ import annotations

import re
from typing import Any

from .models import CanonicalPaperRepresentation


def enforce_pdf_artifact_contract(cpr: CanonicalPaperRepresentation) -> CanonicalPaperRepresentation:
    """Normalize PDF visual-artifact metadata before rendering.

    PDF extraction has multiple producers: embedded images, visual crop
    heuristics, pdfplumber fallback tables, and equation crops. This pass is
    the base contract between ingest and render: each visual artifact gets one
    manifest entry with its caption, placement section, anchor, path, and text
    that must be removed from body prose.
    """
    if not str(cpr.metadata.get("ingest_mode", "") or "").startswith("pdf"):
        return cpr

    cpr.figures = _dedupe_by_label_and_path(cpr.figures, label_getter=lambda item: item.label, path_getter=lambda item: item.path)
    cpr.tables = _dedupe_by_label_and_latex(cpr.tables)
    cpr.metadata["equation_artifacts"] = _dedupe_dict_artifacts(cpr.metadata.get("equation_artifacts", []) or [])

    manifest: list[dict[str, Any]] = []
    figure_section_map = cpr.metadata.get("figure_section_map", {}) or {}
    figure_anchor_map = cpr.metadata.get("figure_anchor_map", {}) or {}
    table_section_map = cpr.metadata.get("table_section_map", {}) or {}
    table_anchor_map = cpr.metadata.get("table_anchor_map", {}) or {}
    table_text_map = cpr.metadata.get("table_text_map", {}) or {}
    equation_text_map = cpr.metadata.get("equation_text_map", {}) or {}

    for order, fig in enumerate(cpr.figures, start=1):
        manifest.append(
            {
                "kind": "figure",
                "label": fig.label,
                "caption": _compact(fig.caption),
                "path": fig.path,
                "section_title": figure_section_map.get(fig.label, ""),
                "anchor_text": figure_anchor_map.get(fig.label, ""),
                "scrub_text": _compact(" ".join([str(fig.caption or ""), str(figure_anchor_map.get(fig.label, "") or "")])),
                "source_order": order,
            }
        )

    valid_tables = []
    for order, table in enumerate(cpr.tables, start=1):
        caption = _compact(table.caption)
        is_visual = _is_visual_table_latex(table.latex)
        if is_visual and not looks_like_pdf_table_caption(caption):
            _append_warning(
                cpr,
                f"Dropped visual table {table.label or order} because its caption looks like body text.",
            )
            continue
        valid_tables.append(table)
        scrub_text = _compact(str(table_text_map.get(table.label, "") or "") or caption)
        table_text_map[table.label] = scrub_text
        manifest.append(
            {
                "kind": "table",
                "label": table.label,
                "caption": caption,
                "path": _first_includegraphics_path(table.latex),
                "section_title": table_section_map.get(table.label, ""),
                "anchor_text": table_anchor_map.get(table.label, ""),
                "scrub_text": scrub_text,
                "source_order": order,
                "span": is_visual,
            }
        )
    cpr.tables = valid_tables
    cpr.metadata["table_text_map"] = table_text_map

    for order, artifact in enumerate(cpr.metadata.get("equation_artifacts", []) or [], start=1):
        if not isinstance(artifact, dict):
            continue
        label = str(artifact.get("label", "") or "")
        scrub_parts = [
            str(artifact.get("raw_text", "") or ""),
            str(equation_text_map.get(label, "") or ""),
            *[str(item or "") for item in artifact.get("scrub_variants", []) or []],
        ]
        manifest.append(
            {
                "kind": "equation",
                "label": label,
                "path": str(artifact.get("path", "") or ""),
                "section_title": str(artifact.get("section_title", "") or ""),
                "anchor_text": str(artifact.get("anchor_text", "") or ""),
                "scrub_text": _compact(" ".join(scrub_parts)),
                "source_order": int(artifact.get("source_order", order) or order),
            }
        )

    cpr.metadata["pdf_artifact_manifest"] = manifest
    cpr.metadata["artifact_scrub_map"] = {
        item["label"]: item["scrub_text"]
        for item in manifest
        if item.get("label") and item.get("scrub_text")
    }
    return cpr


def artifact_scrub_snippets_for_section(cpr: CanonicalPaperRepresentation, section_title: str) -> list[str]:
    snippets: list[str] = []
    if not str(cpr.metadata.get("ingest_mode", "") or "").startswith("pdf"):
        return snippets
    manifest = cpr.metadata.get("pdf_artifact_manifest", []) or []
    for item in manifest:
        if not isinstance(item, dict):
            continue
        if item.get("section_title") and item.get("section_title") != section_title:
            continue
        scrub_text = str(item.get("scrub_text", "") or "").strip()
        if scrub_text:
            snippets.append(scrub_text)
    return snippets


def looks_like_pdf_table_caption(text: str) -> bool:
    compact = _compact(text)
    if len(compact) < 8:
        return False
    if not re.match(r"^[A-Z0-9]", compact):
        return False
    lower = compact.lower()
    if lower.startswith(("shows ", "summarizes ", "presents ", "lists ", "reports ", "is ", "are ")):
        return False
    if len(compact.split()) < 2:
        return False
    if compact[0].islower():
        return False
    return True


def _dedupe_by_label_and_path(items: list, *, label_getter, path_getter) -> list:
    deduped: list = []
    seen_labels: set[str] = set()
    seen_paths: set[str] = set()
    for item in items:
        label = str(label_getter(item) or "")
        path = str(path_getter(item) or "")
        if label and label in seen_labels:
            continue
        if path and path in seen_paths:
            continue
        if label:
            seen_labels.add(label)
        if path:
            seen_paths.add(path)
        deduped.append(item)
    return deduped


def _dedupe_by_label_and_latex(tables: list) -> list:
    deduped: list = []
    seen_labels: set[str] = set()
    seen_latex: set[str] = set()
    for table in tables:
        label = str(getattr(table, "label", "") or "")
        latex = _compact(str(getattr(table, "latex", "") or ""))
        if label and label in seen_labels:
            continue
        if latex and latex in seen_latex:
            continue
        if label:
            seen_labels.add(label)
        if latex:
            seen_latex.add(latex)
        deduped.append(table)
    return deduped


def _dedupe_dict_artifacts(artifacts: list) -> list:
    deduped: list = []
    seen_labels: set[str] = set()
    seen_paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        label = str(artifact.get("label", "") or "")
        path = str(artifact.get("path", "") or "")
        if label and label in seen_labels:
            continue
        if path and path in seen_paths:
            continue
        if label:
            seen_labels.add(label)
        if path:
            seen_paths.add(path)
        deduped.append(artifact)
    return deduped


def _is_visual_table_latex(latex: str) -> bool:
    raw = str(latex or "")
    paths_normalized = raw.replace("\\", "/")
    return "\\includegraphics" in raw and "artifacts/tables/" in paths_normalized


def _first_includegraphics_path(latex: str) -> str:
    match = re.search(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", str(latex or ""))
    return match.group(1) if match else ""


def _append_warning(cpr: CanonicalPaperRepresentation, warning: str) -> None:
    warnings = cpr.metadata.setdefault("warnings", [])
    if warning not in warnings:
        warnings.append(warning)


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()
