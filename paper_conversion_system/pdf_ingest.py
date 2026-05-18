from __future__ import annotations

from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import fitz
import logging
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from .models import CanonicalPaperRepresentation, Figure, Section, Reference, Table
from .pdf_artifact_hygiene import enforce_pdf_artifact_contract, looks_like_pdf_table_caption
from .pdf_cleanup import clean_pdf_text, aggressive_cleanup_pass
from .pdf_doctype import detect_pdf_document_type, extract_thesis_body_text
from .pdf_postprocess import refine_cpr_from_pdf
from .pdf_thesis import parse_thesis_text_to_cpr

logger = logging.getLogger(__name__)
SUPPORTED_PDF_BACKENDS = {"heuristic", "pdfplumber", "grobid"}


def parse_pdf_to_cpr(
    pdf_path: Path,
    source_format_hint: str | None = None,
    cleanup_mode: str = "safe",
    assets_dir: Path | None = None,
    fidelity_mode: str = "preserve",
) -> CanonicalPaperRepresentation:
    """Parse a PDF into CPR.

    This function is the main PDF ingestion entrypoint. It detects document
    type, routes thesis/dissertation PDFs to the thesis-specific parser,
    otherwise applies PDF cleanup, extracts CPR fields, refines the CPR,
    and optionally attempts an aggressive cleanup pass when confidence is high.
    """
    requested_backend = os.environ.get("PAPER_CONVERSION_PDF_BACKEND", "heuristic").strip().lower() or "heuristic"
    backend = requested_backend if requested_backend in SUPPORTED_PDF_BACKENDS else "heuristic"
    grobid_url = (os.environ.get("PAPER_CONVERSION_GROBID_URL", "") or "").strip()
    if backend == "grobid" and grobid_url:
        grobid_cpr = _parse_pdf_with_grobid(pdf_path, source_format_hint, grobid_url, fidelity_mode=fidelity_mode)
        if grobid_cpr is not None:
            return enforce_pdf_artifact_contract(grobid_cpr)
    doc = fitz.open(str(pdf_path))
    try:
        pages = [page.get_text() for page in doc]
        raw_text = "\n\n".join(pages)
        doc_type, doc_meta = detect_pdf_document_type(raw_text)
        extracted_images = _extract_embedded_images(doc, assets_dir)
        (
            page_figures,
            page_tables,
            equation_artifacts,
            figure_section_map,
            figure_anchor_map,
            table_section_map,
            table_anchor_map,
            table_text_map,
            equation_text_map,
        ) = _extract_page_level_visuals(doc, assets_dir, extracted_images)
        page_count = len(doc)
    finally:
        # Release the OS file handle as soon as we've extracted everything we
        # need from PyMuPDF. Subsequent code only needs the in-memory results.
        try:
            doc.close()
        except Exception:
            pass
    suppressed_supplemental_tables = 0
    if backend in {"pdfplumber", "heuristic"}:
        supplemental_tables = _extract_tables_with_pdfplumber(pdf_path, assets_dir)
        if supplemental_tables and not page_tables:
            page_tables.extend(supplemental_tables)
        elif supplemental_tables:
            suppressed_supplemental_tables = len(supplemental_tables)
    if doc_type == "thesis_dissertation":
        cpr = parse_thesis_text_to_cpr(raw_text, source_path=str(pdf_path))
        cpr.metadata["document_type_meta"] = doc_meta
        cpr.metadata["page_count"] = page_count
        if extracted_images:
            cpr.metadata["extracted_figure_assets"] = extracted_images
        return enforce_pdf_artifact_contract(cpr)

    text_source = raw_text
    text, cleanup_meta = clean_pdf_text(text_source, mode=cleanup_mode)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title, authors, metadata = _extract_frontmatter(lines)
    abstract = _extract_abstract(text)
    keywords = _extract_keywords(text)
    sections = _extract_sections(text)
    references = _extract_reference_placeholders(text)
    structured_refs, structured_parser = _try_structured_reference_parse(pdf_path, text)
    if structured_refs:
        references = structured_refs

    metadata.update({
        "source_format": source_format_hint or _guess_format(text),
        "source_path": str(pdf_path),
        "ingest_mode": "pdf",
        "pdf_backend": backend,
        "requested_pdf_backend": requested_backend,
        "page_count": page_count,
        "cleanup": cleanup_meta,
        "document_type": doc_type,
        "document_type_meta": doc_meta,
        "fidelity_mode": fidelity_mode,
        "bibliography_mode": "thebibliography" if fidelity_mode == "preserve" else "bibtex",
        "reference_parser": structured_parser or "regex",
        "extracted_figure_assets": extracted_images,
        "figure_section_map": figure_section_map,
        "figure_anchor_map": figure_anchor_map,
        "table_section_map": table_section_map,
        "table_anchor_map": table_anchor_map,
        "table_text_map": table_text_map,
        "equation_text_map": equation_text_map,
        "equation_artifacts": equation_artifacts,
        "suppressed_supplemental_tables": suppressed_supplemental_tables,
    })
    if suppressed_supplemental_tables:
        metadata.setdefault("warnings", []).append(
            f"Skipped {suppressed_supplemental_tables} supplemental pdfplumber table(s) because "
            "heuristic visual table crops already exist; this avoids duplicate table/image blocks."
        )

    cpr = CanonicalPaperRepresentation(
        title=title or pdf_path.stem,
        authors=authors,
        abstract=abstract,
        keywords=keywords,
        sections=sections,
        references=references,
        metadata=metadata,
    )
    if requested_backend not in SUPPORTED_PDF_BACKENDS:
        cpr.metadata.setdefault("warnings", []).append(
            f"Requested PDF backend '{requested_backend}' is unsupported in this build; using heuristic parser."
        )
    elif backend == "grobid" and not grobid_url:
        cpr.metadata.setdefault("warnings", []).append(
            "Requested PDF backend 'grobid' but PAPER_CONVERSION_GROBID_URL is unset; using heuristic parser."
        )
    elif backend == "grobid":
        cpr.metadata.setdefault("warnings", []).append(
            "Requested PDF backend 'grobid' failed over to heuristic parsing for this file."
        )
    cpr = refine_cpr_from_pdf(cpr)
    cpr = _merge_page_level_visuals(cpr, page_figures, page_tables)
    cpr = _attach_extracted_figure_assets(cpr, extracted_images)

    if doc_type == "thesis_dissertation":
        cpr.metadata.setdefault("warnings", []).append(
            "Detected thesis/dissertation-style PDF; using alternate body-focused parsing stub. Output quality may be lower than paper-native parsing."
        )

    if cpr.metadata.get("ingest_confidence", 0) >= 0.75 and cleanup_mode == "safe":
        aggressive_text = aggressive_cleanup_pass(text)
        aggressive_sections = _extract_sections(aggressive_text)
        if len(aggressive_sections) >= max(3, len(cpr.sections) - 1):
            cpr.sections = aggressive_sections
            cpr.metadata["cleanup"]["aggressive_applied"] = True
            cpr = refine_cpr_from_pdf(cpr)
            cpr = _merge_page_level_visuals(cpr, page_figures, page_tables)
            cpr = _attach_extracted_figure_assets(cpr, extracted_images)
        else:
            cpr.metadata["cleanup"]["aggressive_applied"] = False
    else:
        cpr.metadata["cleanup"]["aggressive_applied"] = False

    return enforce_pdf_artifact_contract(cpr)


def _extract_embedded_images(doc: fitz.Document, assets_dir: Path | None) -> list[str]:
    """Extract non-trivial embedded raster images from a PDF.

    The PDF path is used only for ingestion, so any recovered image assets must
    be written into the job workspace before rendering. We filter out tiny
    decorative assets like logos/icons to avoid polluting the converted output.
    """
    if assets_dir is None:
        return []

    assets_dir.mkdir(parents=True, exist_ok=True)
    seen_xrefs: set[int] = set()
    extracted: list[str] = []
    min_dimension = 120
    min_area = 24_000

    for page_index in range(doc.page_count):
        page = doc[page_index]
        for image_index, image_meta in enumerate(page.get_images(full=True), start=1):
            xref = image_meta[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                payload = doc.extract_image(xref)
            except Exception:
                continue
            width = int(payload.get("width") or 0)
            height = int(payload.get("height") or 0)
            if width < min_dimension or height < min_dimension or (width * height) < min_area:
                continue
            image_bytes = payload.get("image")
            ext = str(payload.get("ext") or "png").lower()
            if not image_bytes:
                continue
            filename = f"figure_p{page_index + 1}_{image_index}.{ext}"
            out_path = assets_dir / filename
            out_path.write_bytes(image_bytes)
            extracted.append(f"figures/{filename}")
    return extracted


def _parse_pdf_with_grobid(
    pdf_path: Path,
    source_format_hint: str | None,
    grobid_url: str,
    fidelity_mode: str,
) -> CanonicalPaperRepresentation | None:
    """Parse a PDF via GROBID fulltext endpoint when configured."""
    endpoint = grobid_url.rstrip("/") + "/api/processFulltextDocument"
    boundary = "----PaperConvBoundary"
    payload = pdf_path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="input"; filename="{pdf_path.name}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            xml_data = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("grobid_parse_failed pdf=%s url=%s err=%s", pdf_path, grobid_url, exc)
        return None

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        logger.warning("grobid_xml_invalid pdf=%s err=%s", pdf_path, exc)
        return None

    ns = {"tei": "http://www.tei-c.org/ns/1.0"}

    def text_of(path: str) -> str:
        el = root.find(path, ns)
        if el is None:
            return ""
        return " ".join("".join(el.itertext()).split())

    title = text_of(".//tei:titleStmt/tei:title")
    authors: list[str] = []
    for author in root.findall(".//tei:titleStmt/tei:author", ns):
        pers = author.find(".//tei:persName", ns)
        if pers is None:
            continue
        name = " ".join("".join(pers.itertext()).split())
        if name:
            authors.append(name)

    abstract = text_of(".//tei:profileDesc/tei:abstract")
    sections: list[Section] = []
    for div in root.findall(".//tei:text/tei:body/tei:div", ns):
        head = div.find("./tei:head", ns)
        heading = " ".join("".join(head.itertext()).split()) if head is not None else "Section"
        paragraphs = []
        for p in div.findall("./tei:p", ns):
            para = " ".join("".join(p.itertext()).split())
            if para:
                paragraphs.append(para)
        if paragraphs:
            sections.append(Section(title=heading or "Section", content="\n\n".join(paragraphs)))

    references: list[Reference] = []
    for idx, bibl in enumerate(root.findall(".//tei:listBibl/tei:biblStruct", ns), start=1):
        raw = " ".join("".join(bibl.itertext()).split())
        if raw:
            references.append(Reference(key=f"ref{idx}", raw=raw))

    metadata = {
        "source_format": source_format_hint or "unknown",
        "source_path": str(pdf_path),
        "ingest_mode": "pdf",
        "pdf_backend": "grobid",
        "fidelity_mode": fidelity_mode,
        "reference_parser": "grobid",
        "warnings": [],
    }
    if not sections:
        metadata["warnings"].append("GROBID returned no section blocks; local fallback may be preferable for this file.")
    return CanonicalPaperRepresentation(
        title=title or pdf_path.stem,
        authors=authors,
        abstract=abstract,
        keywords=[],
        sections=sections or [Section(title="Body", content=text_of(".//tei:text/tei:body"))],
        references=references,
        metadata=metadata,
    )


def _attach_extracted_figure_assets(
    cpr: CanonicalPaperRepresentation,
    extracted_images: list[str],
) -> CanonicalPaperRepresentation:
    if not extracted_images:
        return cpr

    warnings = cpr.metadata.setdefault("warnings", [])

    # Track which extracted images are already attached to a figure so we
    # don't re-emit them as duplicate ``fig:extracted{i}`` placeholders.
    already_used = {fig.path for fig in cpr.figures if fig.path}
    unused_images = [p for p in extracted_images if p not in already_used]

    # Pair *unused* images with figures that still lack a path, in order.
    # This avoids overwriting the careful page-level pairing done earlier.
    placeholders = [fig for fig in cpr.figures if not fig.path]
    paired = 0
    for fig, image_path in zip(placeholders, unused_images):
        fig.path = image_path
        fig.placement = "H"
        paired += 1
    unused_images = unused_images[paired:]

    # Any leftover images get appended as generic figure entries — but only
    # when the document has no captioned figures at all (so we never inflate
    # an already-correct figure list with phantom duplicates).
    if unused_images and not cpr.figures:
        for index, image_path in enumerate(unused_images, start=1):
            cpr.figures.append(
                Figure(
                    label=f"fig:extracted{index}",
                    caption=f"Extracted figure {index}",
                    path=image_path,
                    placement="H",
                )
            )
        warnings.append(
            f"No figure captions detected; emitted {len(unused_images)} generic figure entries for embedded images."
        )
    elif unused_images:
        # Keep them noted but do NOT silently inject new figure floats — they
        # were probably logos, decorative banners, or already-attached images
        # that survived deduplication.
        warnings.append(
            f"Skipped {len(unused_images)} embedded image asset(s) without a matching caption "
            "to avoid duplicate figure placeholders."
        )

    # Final dedup: collapse any figures that ended up sharing the same image
    # path (keep the entry with the longest caption / a real label).
    if cpr.figures:
        by_path: dict[str, Figure] = {}
        deduped: list[Figure] = []
        for fig in cpr.figures:
            key = fig.path or f"__nopath__:{fig.label}"
            existing = by_path.get(key)
            if existing is None:
                by_path[key] = fig
                deduped.append(fig)
                continue
            # Same image already attached to another figure — merge captions
            # and drop this duplicate.
            if len(fig.caption) > len(existing.caption):
                existing.caption = fig.caption
            if not existing.label.startswith("fig:") and fig.label.startswith("fig:"):
                existing.label = fig.label
        if len(deduped) != len(cpr.figures):
            warnings.append(
                f"Removed {len(cpr.figures) - len(deduped)} duplicate figure entries that shared the same image asset."
            )
            cpr.figures = deduped

    return cpr


def _extract_page_level_visuals(
    doc: fitz.Document,
    assets_dir: Path | None,
    extracted_images: list[str],
) -> tuple[list[Figure], list[Table], list[dict], dict[str, str], dict[str, str], dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    figures: list[Figure] = []
    tables: list[Table] = []
    equation_artifacts: list[dict] = []
    figure_section_map: dict[str, str] = {}
    figure_anchor_map: dict[str, str] = {}
    table_section_map: dict[str, str] = {}
    table_anchor_map: dict[str, str] = {}
    table_text_map: dict[str, str] = {}
    equation_text_map: dict[str, str] = {}
    images_by_page = _image_paths_by_page(extracted_images)
    # Track figure labels we have already seen across the whole document so a
    # body-text reference (e.g. "Figure 1 shows ...") that re-mentions an
    # existing figure number does not append a second placeholder Figure entry
    # — and, more importantly, does not consume an image from ``image_iter``,
    # which would shift every subsequent figure's image off by one.
    seen_fig_labels: set[str] = set()
    artifact_root = assets_dir.parent / "artifacts" if assets_dir is not None else None
    figure_root = assets_dir if assets_dir is not None else None
    table_root = artifact_root / "tables" if artifact_root is not None else None
    equation_root = artifact_root / "equations" if artifact_root is not None else None

    if figure_root is not None:
        figure_root.mkdir(parents=True, exist_ok=True)
    if table_root is not None:
        table_root.mkdir(parents=True, exist_ok=True)
    if equation_root is not None:
        equation_root.mkdir(parents=True, exist_ok=True)

    for page_index in range(doc.page_count):
        page = doc[page_index]
        lines = _collect_page_lines(page.get_text("dict"))
        for idx, line in enumerate(lines):
            # Only treat lines that look like *real* figure captions as such.
            # A real caption begins with "Fig." / "Figure" + a number followed
            # by a caption terminator (period, colon, dash, or end-of-line).
            # Body text such as "Figure 1 shows the architecture overview"
            # would otherwise be consumed as a caption — duplicating figures
            # and shifting the image-iterator alignment for every later
            # figure on the page.
            fig_match = re.match(
                r"(?:Fig\.?|Figure)\s*(\d+)\s*(?:[.:\-–—]\s*(.*))?$",
                line["text"],
                flags=re.I,
            )
            if fig_match:
                number = fig_match.group(1)
                caption = (fig_match.group(2) or "").strip(" .:-")
                # If the regex did not see a terminator AND the line continues
                # with a lowercase verb-like word, it is almost certainly a
                # cross-reference, not a caption.
                if fig_match.group(2) is None and re.search(
                    r"^(?:Fig\.?|Figure)\s*\d+\s+[a-z]",
                    line["text"],
                ):
                    continue
                if not caption and idx + 1 < len(lines):
                    caption = lines[idx + 1]["text"].strip(" .:-")
                label = f"fig:{number}"
                if label in seen_fig_labels:
                    # Already captured this figure earlier in the document —
                    # do not consume another image from the iterator.
                    continue
                if caption:
                    seen_fig_labels.add(label)
                    image_rel = _pop_page_image(images_by_page, page_index + 1)
                    if not image_rel and figure_root is not None:
                        figure_bbox = _detect_figure_region(page, lines, idx)
                        if figure_bbox is not None:
                            image_rel = _crop_region(
                                page,
                                figure_bbox,
                                figure_root / f"figure_p{page_index + 1}_{number}.png",
                                rel_prefix="figures",
                            )
                    figures.append(
                        Figure(
                            label=label,
                            caption=_cleanup(caption),
                            path=image_rel,
                            placement="H",
                        )
                    )
                    figure_section_map[label] = _infer_section_title(lines, idx)
                    figure_anchor_map[label] = _anchor_from_lines(lines, idx)

        for table_idx, region in enumerate(_detect_table_regions(lines, page.rect.width), start=1):
            caption = region["caption"]
            label = region["label"]
            image_rel = ""
            if table_root is not None:
                crop_bbox = _expand_table_crop_bbox(
                    page,
                    region.get("visual_bbox") or region["bbox"],
                )
                image_rel = _crop_region(
                    page,
                    crop_bbox,
                    table_root / f"table_p{page_index + 1}_{table_idx}.png",
                    rel_prefix="artifacts/tables",
                )
            latex = _table_lines_to_latex(region["data_lines"])
            if image_rel:
                latex = "\\centering\n" + (
                    f"\\includegraphics[width=\\linewidth,height=0.34\\textheight,keepaspectratio]"
                    f"{{{image_rel}}}"
                )
            tables.append(Table(label=label, caption=_cleanup(caption), latex=latex, placement="H"))
            table_section_map[label] = region["section_title"]
            table_anchor_map[label] = region["anchor_text"]
            raw_text = region["raw_text"]
            if image_rel and crop_bbox.width > page.rect.width * 0.62:
                raw_text = _cleanup(f"{raw_text} {_table_crop_text(lines, crop_bbox)}")
            table_text_map[label] = raw_text

        for eq_idx, equation in enumerate(_detect_equation_regions(lines, page.rect.width), start=1):
            if equation_root is None:
                continue
            label = f"eqimg:{page_index + 1}:{eq_idx}"
            image_rel = _crop_region(
                page,
                equation["bbox"],
                equation_root / f"equation_p{page_index + 1}_{eq_idx}.png",
                rel_prefix="artifacts/equations",
            )
            equation_artifacts.append(
                {
                    "label": label,
                    "path": image_rel,
                    "anchor_text": equation["anchor_text"],
                    "section_title": equation["section_title"],
                    "equation_number": equation.get("equation_number", ""),
                    "source_order": page_index * 10000 + int(equation.get("source_order", eq_idx)),
                    "page_number": page_index + 1,
                    "placement": "H",
                    "raw_text": equation["raw_text"],
                    "raw_lines": equation.get("raw_lines", []),
                    "scrub_variants": equation.get("scrub_variants", []),
                }
            )
            equation_text_map[label] = equation["raw_text"]

    return (
        figures,
        tables,
        equation_artifacts,
        figure_section_map,
        figure_anchor_map,
        table_section_map,
        table_anchor_map,
        table_text_map,
        equation_text_map,
    )


def _table_lines_to_latex(lines: list[str]) -> str:
    cleaned = [re.sub(r"\s+", " ", line).strip() for line in lines if line.strip()]
    cleaned = [
        line for line in cleaned
        if not re.search(r"Authorized licensed use|IEEE .*Conference|Downloaded on", line, re.I)
    ]
    if len(cleaned) < 3:
        return "% reconstructed table unavailable"

    header_cols = 3 if len(cleaned) >= 6 else min(3, len(cleaned))
    headers = cleaned[:header_cols]
    body = cleaned[header_cols:]
    rows = [headers]
    if body:
        for start in range(0, len(body), header_cols):
            row = body[start:start + header_cols]
            if len(row) == header_cols:
                rows.append(row)

    if len(rows) < 2:
        return "% reconstructed table unavailable"

    def esc(cell: str) -> str:
        return (
            cell.replace("\\", r"\textbackslash{}")
            .replace("&", r"\&")
            .replace("%", r"\%")
            .replace("#", r"\#")
            .replace("_", r"\_")
        )

    col_spec = " | ".join(["l"] * len(rows[0]))
    latex_lines = [f"\\begin{{tabular}}{{{col_spec}}}", "\\hline"]
    for row_idx, row in enumerate(rows):
        latex_lines.append(" & ".join(esc(cell) for cell in row) + r" \\")
        if row_idx == 0:
            latex_lines.append("\\hline")
    latex_lines.append("\\hline")
    latex_lines.append("\\end{tabular}")
    return "\n".join(latex_lines)


def _image_paths_by_page(paths: list[str]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for path in paths:
        match = re.search(r"(?:^|[/\\])figure_p(\d+)_\d+\.", path, flags=re.I)
        if not match:
            continue
        grouped.setdefault(int(match.group(1)), []).append(path)
    return grouped


def _pop_page_image(grouped: dict[int, list[str]], page_number: int) -> str:
    page_images = grouped.get(page_number) or []
    if not page_images:
        return ""
    return page_images.pop(0)


def _extract_tables_with_pdfplumber(pdf_path: Path, assets_dir: Path | None) -> list[Table]:
    """Optional table extraction pass using pdfplumber for PDF ingest fidelity."""
    try:
        import pdfplumber  # type: ignore
    except Exception:
        return []

    out: list[Table] = []
    artifact_root = assets_dir.parent / "artifacts" / "tables" if assets_dir is not None else None
    if artifact_root is not None:
        artifact_root.mkdir(parents=True, exist_ok=True)

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_idx, page in enumerate(pdf.pages, start=1):
                tables = page.extract_tables() or []
                for idx, table_data in enumerate(tables, start=1):
                    rows = [[(cell or "").strip() for cell in row] for row in table_data if row]
                    rows = [row for row in rows if any(row)]
                    if len(rows) < 2:
                        continue
                    col_count = max(len(row) for row in rows)
                    norm_rows: list[list[str]] = []
                    for row in rows:
                        padded = row + [""] * (col_count - len(row))
                        norm_rows.append(padded[:col_count])
                    latex_lines = [f"\\begin{{tabular}}{{{' | '.join(['l'] * col_count)}}}", "\\hline"]
                    for r_idx, row in enumerate(norm_rows):
                        safe = [
                            cell.replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")
                            for cell in row
                        ]
                        latex_lines.append(" & ".join(safe) + r" \\")
                        if r_idx == 0:
                            latex_lines.append("\\hline")
                    latex_lines.extend(["\\hline", "\\end{tabular}"])
                    label = f"tab:plumber:{page_idx}:{idx}"
                    caption = f"Extracted table {idx} (page {page_idx})"
                    out.append(Table(label=label, caption=caption, latex="\n".join(latex_lines), placement="H"))
    except Exception as exc:
        logger.warning("pdfplumber_table_extract_failed pdf=%s err=%s", pdf_path, exc)
        return []

    if out:
        logger.info("pdfplumber_table_extract_ok pdf=%s count=%d", pdf_path, len(out))
    return out


def _collect_page_lines(page_dict: dict) -> list[dict]:
    lines: list[dict] = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text:
                continue
            bbox = tuple(line.get("bbox") or block.get("bbox"))
            lines.append({"text": _cleanup(text), "bbox": bbox})
    lines.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    return lines


def _anchor_from_lines(
    lines: list[dict],
    idx: int,
    max_lines: int = 4,
    origin_bbox: tuple[float, float, float, float] | None = None,
    page_width: float | None = None,
) -> str:
    anchor_lines: list[str] = []
    for candidate in reversed(lines[:idx]):
        text = candidate["text"]
        if not text:
            continue
        if origin_bbox is not None and page_width is not None and not _same_column(candidate["bbox"], origin_bbox, page_width):
            continue
        if re.match(r"(?:TABLE\s+[IVXLC0-9]+|Fig\.\s*\d+|REFERENCES$)", text, flags=re.I):
            continue
        if _looks_like_section_heading_line(text):
            continue
        anchor_lines.append(text)
        if len(anchor_lines) >= max_lines:
            break
    anchor_lines.reverse()
    return _cleanup(" ".join(anchor_lines))


def _infer_section_title(lines: list[dict], idx: int) -> str:
    for candidate in reversed(lines[: idx + 1]):
        match = re.match(r"([IVX]+)\.\s+([A-Z][A-Z\s\-]+)$", candidate["text"])
        if match:
            return _normalize_heading(candidate["text"])
    return ""


def _looks_like_section_heading_line(text: str) -> bool:
    compact = text.strip()
    if re.match(r"(?:[IVX]+|\d+)\.\s+[A-Z][A-Z\s\-]+$", compact):
        return True
    if re.match(r"[A-Z]\.\s+[A-Z][A-Za-z'\-]*(?:\s+[A-Z][A-Za-z'\-]*){0,7}$", compact):
        return True
    return False


def _detect_table_regions(lines: list[dict], page_width: float) -> list[dict]:
    regions: list[dict] = []
    idx = 0
    while idx < len(lines):
        heading = _parse_table_heading(lines[idx]["text"])
        if heading is None:
            idx += 1
            continue
        label = f"tab:{heading['number'].lower()}"
        origin_bbox = lines[idx]["bbox"]
        section_title = _infer_section_title(lines, idx)
        anchor_text = _anchor_from_lines(lines, idx, origin_bbox=origin_bbox, page_width=page_width)
        region_lines = [lines[idx]]
        end = idx + 1
        last_y = origin_bbox[3]
        while end < len(lines):
            current_line = lines[end]
            current = current_line["text"]
            if _same_column(current_line["bbox"], origin_bbox, page_width):
                if len(region_lines) > 1 and (current_line["bbox"][1] - last_y) > 42:
                    break
                if re.match(r"(?:TABLE\s+[IVXLC0-9]+|[A-Z]\.\s+[A-Z]|[IVX]+\.\s+[A-Z]|REFERENCES$)", current, flags=re.I):
                    break
                if len(region_lines) >= 3 and _looks_like_body_paragraph(current):
                    break
                region_lines.append(current_line)
                last_y = current_line["bbox"][3]
            elif len(region_lines) > 1 and current_line["bbox"][1] > last_y + 42:
                break
            end += 1
        if heading["caption"]:
            caption, caption_idx = _extend_table_caption_from_index(region_lines, heading["caption"], 0)
        else:
            caption, caption_idx = _select_table_caption(region_lines, label, page_width)
            caption, caption_idx = _extend_table_caption_from_index(region_lines, caption, caption_idx)
        data_line_objs = region_lines[caption_idx + 1:]
        while data_line_objs and _looks_like_table_trailing_body_line(data_line_objs[-1]["text"]):
            data_line_objs = data_line_objs[:-1]
        data_lines = [line["text"] for line in data_line_objs]
        if len(data_lines) < 2:
            idx = end
            continue
        bbox = _union_bboxes([line["bbox"] for line in region_lines])
        visual_bbox = _union_bboxes([line["bbox"] for line in data_line_objs])
        regions.append(
            {
                "label": label,
                "caption": caption,
                "bbox": bbox,
                "visual_bbox": visual_bbox,
                "data_lines": data_lines,
                "section_title": section_title,
                "anchor_text": anchor_text,
                "raw_text": _cleanup(" ".join([caption, *data_lines])),
            }
        )
        idx = end
    return regions


def _parse_table_heading(text: str) -> dict[str, str] | None:
    match = re.match(r"^TABLE\s+([IVXLC]+|\d+)\s*(.*)$", text.strip(), flags=re.I)
    if not match:
        return None
    caption = match.group(2).strip(" .:-,")
    if caption and not _looks_like_table_caption_text(caption):
        return None
    return {"number": match.group(1), "caption": caption}


def _looks_like_table_caption_text(text: str) -> bool:
    return looks_like_pdf_table_caption(text)


def _extend_table_caption_from_index(region_lines: list[dict], caption: str, caption_idx: int) -> tuple[str, int]:
    caption_parts = [caption.strip()]
    for idx, line in enumerate(region_lines[caption_idx + 1:], start=caption_idx + 1):
        text = line["text"].strip()
        if not text:
            break
        if _looks_like_table_data_line(text) or _looks_like_table_grid_header(text):
            break
        if _looks_like_table_caption_continuation(text):
            caption_parts.append(text.strip(" .:-"))
            caption_idx = idx
            continue
        break
    return _cleanup(" ".join(part for part in caption_parts if part)), caption_idx


def _looks_like_table_grid_header(text: str) -> bool:
    compact = text.strip()
    if not compact:
        return False
    if len(compact.split()) <= 6 and re.search(r"\b(?:Scheme|Client|Server|Time|Length|Password|Biometric|Phone)\b", compact):
        return True
    return False


def _looks_like_table_caption_continuation(text: str) -> bool:
    compact = text.strip()
    if len(compact) < 6:
        return False
    if _looks_like_table_data_line(compact) or _looks_like_table_grid_header(compact):
        return False
    letters = [ch for ch in compact if ch.isalpha()]
    if not letters:
        return False
    uppercase = sum(1 for ch in letters if ch.isupper())
    return uppercase / len(letters) >= 0.75


def _looks_like_table_trailing_body_line(text: str) -> bool:
    compact = text.strip()
    if not compact:
        return True
    if _looks_like_table_data_line(compact):
        return False
    if _looks_like_table_grid_header(compact):
        return False
    if compact[0].islower():
        return True
    words = compact.split()
    lowercase_words = sum(1 for word in words if any(ch.islower() for ch in word))
    return len(words) >= 5 and lowercase_words >= max(3, len(words) // 2)


def _detect_equation_regions(lines: list[dict], page_width: float) -> list[dict]:
    regions: list[dict] = []
    idx = 0
    while idx < len(lines):
        if not _looks_like_equation_line(lines[idx]["text"], lines[idx]["bbox"], page_width):
            idx += 1
            continue
        origin_bbox = lines[idx]["bbox"]
        region_lines = [lines[idx]]
        region_bbox = fitz.Rect(origin_bbox)

        start = idx - 1
        while start >= 0:
            current_line = lines[start]
            if (region_bbox.y0 - current_line["bbox"][3]) > 20:
                break
            if _equation_line_should_join(current_line, region_bbox, page_width):
                region_lines.insert(0, current_line)
                region_bbox = _union_bboxes([line["bbox"] for line in region_lines])
            start -= 1

        end = idx + 1
        while end < len(lines):
            current_line = lines[end]
            if current_line["bbox"][1] > region_bbox.y1 + 26:
                break
            if _equation_line_should_join(current_line, region_bbox, page_width):
                region_lines.append(current_line)
                region_bbox = _union_bboxes([line["bbox"] for line in region_lines])
            end += 1

        if len(region_lines) < 2 and not _has_nearby_equation_context(lines, idx, page_width):
            idx += 1
            continue

        raw_lines = [line["text"] for line in region_lines]
        if not _equation_region_has_display_math(raw_lines):
            idx = end
            continue
        bbox = _union_bboxes([line["bbox"] for line in region_lines])
        equation_number = _extract_equation_number(raw_lines)
        regions.append(
            {
                "bbox": bbox,
                "section_title": _infer_section_title(lines, idx),
                "anchor_text": _anchor_from_lines(lines, idx, origin_bbox=origin_bbox, page_width=page_width),
                "equation_number": equation_number,
                "source_order": int(bbox.y0 * 10),
                "raw_text": _cleanup(_linearize_equation_lines(raw_lines)),
                "raw_lines": raw_lines,
                "scrub_variants": _equation_scrub_variants(raw_lines),
            }
        )
        idx = end
    return regions


def _looks_like_equation_line(text: str, bbox: tuple[float, float, float, float], page_width: float) -> bool:
    compact = text.strip()
    if len(compact) < 3 or len(compact) > 120:
        return False
    if compact.endswith("."):
        return False
    if _looks_like_body_paragraph(compact):
        return False
    width = bbox[2] - bbox[0]
    centered = abs(((bbox[0] + bbox[2]) / 2) - (page_width / 2)) < (page_width * 0.18)
    narrow = width < (page_width * 0.7)
    has_math = bool(re.search(r"(?:[+\-*/^_()]|log|sqrt)", compact)) or any(ch in compact for ch in ("γ", "√", "Σ", "∫"))
    if not (centered or narrow):
        return False
    lowered = compact.lower()
    if lowered.startswith(("where ", "table ", "actual ", "estimated ", "difference ")):
        return False
    if len(compact.split()) > 4 and "=" not in compact and not re.search(r"[\d()]", compact):
        return False
    strong_math = bool(re.search(r"(?:=|[+*/^_]|(?<=[0-9)])\s*-\s*(?=[0-9A-Za-z(])|log|sqrt|×|−|≤|≥)", compact))
    strong_math = strong_math or any(ch in compact for ch in ("γ", "√", "Σ", "∫"))
    if "=" in compact and len(re.findall(r"\d+", compact)) >= 5 and not re.search(r"[+*/()]", compact):
        return False
    symbolic_run = bool(re.search(r"[A-Za-z0-9)]\s*(?:[+*/=])\s*[A-Za-z0-9(]", compact))
    return strong_math or symbolic_run
    has_math = bool(re.search(r"(?:=|[+\-*/^_()]|log|sqrt|×|−|≤|≥)", compact))
    has_math = has_math or any(ch in compact for ch in ("Î³", "γ", "âˆš", "√", "Î£", "Σ", "âˆ«", "∫"))
    has_grouping = bool(re.search(r"\([^)]*[A-Za-z0-9][^)]*\)", compact))
    has_symbolic_run = bool(re.search(r"[A-Za-z]\s*[+\-×*/=]\s*[A-Za-z0-9(]", compact))
    return has_math or has_grouping or has_symbolic_run


def _equation_line_should_join(line: dict, region_bbox: fitz.Rect, page_width: float) -> bool:
    bbox = line["bbox"]
    text = line["text"]
    if not _equation_horizontally_related(bbox, region_bbox, page_width):
        return False
    if _looks_like_equation_line(text, bbox, page_width):
        return True
    return _looks_like_equation_satellite_line(text, bbox, page_width)


def _equation_horizontally_related(
    bbox: tuple[float, float, float, float],
    region_bbox: fitz.Rect,
    page_width: float,
) -> bool:
    candidate_center = (bbox[0] + bbox[2]) / 2
    region_center = (region_bbox.x0 + region_bbox.x1) / 2
    if abs(candidate_center - region_center) <= (page_width * 0.18):
        return True
    if bbox[2] >= region_bbox.x0 - 24 and bbox[0] <= region_bbox.x1 + 84:
        return True
    return _column_bucket(bbox, page_width) == _column_bucket((region_bbox.x0, region_bbox.y0, region_bbox.x1, region_bbox.y1), page_width)


def _looks_like_equation_satellite_line(text: str, bbox: tuple[float, float, float, float], page_width: float) -> bool:
    compact = text.strip()
    if not compact or len(compact) > 48:
        return False
    if compact.endswith("."):
        return False
    width = bbox[2] - bbox[0]
    centered = abs(((bbox[0] + bbox[2]) / 2) - (page_width / 2)) < (page_width * 0.18)
    narrow = width < (page_width * 0.55)
    if not (centered or narrow):
        return False
    if re.fullmatch(r"\(\d+\)", compact):
        return True
    if re.fullmatch(r"[A-Za-z]\d*", compact):
        return True
    if re.fullmatch(r"\d+", compact):
        return True
    if _looks_like_body_paragraph(compact):
        return False
    if len(compact.split()) > 4 and not re.search(r"[\d()]", compact):
        return False
    if len(compact.split()) > 6:
        return False
    return bool(
        re.search(r"(?:=|[+*/^_]|(?<=[0-9)])\s*-\s*(?=[0-9A-Za-z(])|log|sqrt|×|−|≤|≥)", compact)
        or any(ch in compact for ch in ("γ", "√", "Σ", "∫"))
    )
    return bool(
        re.search(r"(?:[+\-*/^_()=]|log|sqrt|×|−|≤|≥)", compact)
        or any(ch in compact for ch in ("Î³", "γ", "âˆš", "√", "Î£", "Σ", "âˆ«", "∫"))
    )


def _has_nearby_equation_context(lines: list[dict], idx: int, page_width: float) -> bool:
    anchor_bbox = lines[idx]["bbox"]
    for offset in (-2, -1, 1, 2):
        probe_idx = idx + offset
        if probe_idx < 0 or probe_idx >= len(lines):
            continue
        probe = lines[probe_idx]
        if abs(probe["bbox"][1] - anchor_bbox[1]) > 36:
            continue
        if _equation_horizontally_related(probe["bbox"], fitz.Rect(anchor_bbox), page_width) and (
            _looks_like_equation_line(probe["text"], probe["bbox"], page_width)
            or _looks_like_equation_satellite_line(probe["text"], probe["bbox"], page_width)
        ):
            return True
    return False


def _extract_equation_number(raw_lines: list[str]) -> str:
    for raw in raw_lines:
        match = re.fullmatch(r"\((\d+)\)", _cleanup(raw))
        if match:
            return match.group(1)
    for raw in raw_lines:
        match = re.search(r"\((\d+)\)\s*$", _cleanup(raw))
        if match:
            return match.group(1)
    return ""


def _linearize_equation_lines(raw_lines: list[str]) -> str:
    chunks: list[str] = []
    equation_numbers: list[str] = []
    pending_prefix: list[str] = []

    for raw in raw_lines:
        line = _cleanup(raw)
        if not line:
            continue
        if re.fullmatch(r"\(\d+\)", line):
            equation_numbers.append(line)
            continue
        if _is_short_equation_satellite(line):
            if chunks:
                chunks[-1] = f"{chunks[-1]} {line}".strip()
            else:
                pending_prefix.append(line)
            continue
        if pending_prefix:
            line = " ".join([line, *pending_prefix]).strip()
            pending_prefix.clear()
        if chunks and (_equation_chunk_needs_continuation(chunks[-1]) or _equation_fragment_belongs_to_previous(line)):
            chunks[-1] = f"{chunks[-1]} {line}".strip()
        else:
            chunks.append(line)

    if pending_prefix:
        if chunks:
            chunks[-1] = f"{chunks[-1]} {' '.join(pending_prefix)}".strip()
        else:
            chunks.extend(pending_prefix)

    if equation_numbers:
        chunks.extend(equation_numbers)
    return " ".join(chunks).strip()


def _equation_scrub_variants(raw_lines: list[str]) -> list[str]:
    cleaned_lines = [_cleanup(line) for line in raw_lines if _cleanup(line)]
    if not cleaned_lines:
        return []

    variants: list[str] = []

    def add(candidate: str) -> None:
        normalized = _cleanup(candidate)
        if normalized and normalized not in variants:
            variants.append(normalized)

    add(" ".join(cleaned_lines))
    add(_linearize_equation_lines(cleaned_lines))

    non_numbers = [line for line in cleaned_lines if not re.fullmatch(r"\(\d+\)", line)]
    equation_numbers = [line for line in cleaned_lines if re.fullmatch(r"\(\d+\)", line)]
    if len(non_numbers) >= 2:
        operator_fragments = [line for line in non_numbers[1:] if re.search(r"[×+\-*/=]", line)]
        variable_fragments = [line for line in non_numbers[1:] if re.fullmatch(r"[A-Za-z]\d*", line)]
        if operator_fragments and variable_fragments:
            add(" ".join([non_numbers[0], *variable_fragments, *operator_fragments, *equation_numbers]))
            for operator in operator_fragments:
                if "×" in operator:
                    add(" ".join([non_numbers[0], *variable_fragments, operator.replace("×", "x"), *equation_numbers]))
        assignment_fragments = [line for line in non_numbers if line.endswith("=")]
        formula_fragments = [line for line in non_numbers if line not in assignment_fragments and re.search(r"[()]", line)]
        if assignment_fragments and formula_fragments:
            add(" ".join([assignment_fragments[0], *variable_fragments, *formula_fragments, *equation_numbers]))

    for variant in list(variants):
        if "×" in variant:
            add(variant.replace("×", "x"))

    return variants


def _equation_region_has_display_math(raw_lines: list[str]) -> bool:
    for raw in raw_lines:
        compact = _cleanup(raw)
        if not compact:
            continue
        if re.search(r"(?:[+*/^_]|log|sqrt|×|√|γ|Σ|∫)", compact):
            return True
        if "=" in compact and not re.fullmatch(r"[A-Za-z]\d*\s*=\s*\d+(?:\.\d+)?", compact):
            return True
    return False


def _is_short_equation_satellite(text: str) -> bool:
    return bool(re.fullmatch(r"(?:[A-Za-z]\d*|\d+)", text))


def _equation_chunk_needs_continuation(text: str) -> bool:
    return text.endswith(("=", "+", "-", "×", "/", "(", "{"))


def _equation_fragment_belongs_to_previous(text: str) -> bool:
    compact = text.strip()
    return compact.startswith(("(", "×", "√")) or bool(re.match(r"^[A-Za-z]\d*$", compact))


def _same_column(
    bbox_a: tuple[float, float, float, float],
    bbox_b: tuple[float, float, float, float],
    page_width: float,
) -> bool:
    return _column_bucket(bbox_a, page_width) == _column_bucket(bbox_b, page_width)


def _column_bucket(bbox: tuple[float, float, float, float], page_width: float) -> str:
    center_x = (bbox[0] + bbox[2]) / 2
    if center_x < page_width * 0.43:
        return "left"
    if center_x > page_width * 0.57:
        return "right"
    return "center"


def _detect_figure_region(page: fitz.Page, lines: list[dict], caption_idx: int) -> fitz.Rect | None:
    caption = lines[caption_idx]
    caption_bbox = caption["bbox"]
    page_width = page.rect.width
    column = _column_bucket(caption_bbox, page_width)
    top = max(28.0, caption_bbox[1] - 260.0)
    bottom = caption_bbox[1] - 2.0

    candidates: list[tuple[float, float, float, float]] = []
    for bbox in _page_nontext_bboxes(page):
        if _bbox_in_figure_band(bbox, caption_bbox, column, page_width, top, bottom):
            candidates.append(bbox)

    for line in lines[:caption_idx]:
        bbox = line["bbox"]
        text = line["text"]
        if not _bbox_in_figure_band(bbox, caption_bbox, column, page_width, top, bottom):
            continue
        if _line_should_not_be_cropped_as_figure(text):
            continue
        candidates.append(bbox)

    if not candidates:
        return None

    region = _union_bboxes(candidates)
    if region.height < 18 or region.width < 24:
        return None
    x0, x1 = _figure_column_bounds(column, page_width)
    region.x0 = max(x0, min(region.x0, caption_bbox[0] - 24))
    region.x1 = min(x1, max(region.x1, caption_bbox[2] + 24))
    region.y0 = max(28.0, region.y0 - 4)
    region.y1 = min(caption_bbox[1] - 2, region.y1 + 4)
    if region.height < 18 or region.width < 24:
        return None
    return region


def _page_nontext_bboxes(page: fitz.Page) -> list[tuple[float, float, float, float]]:
    bboxes: list[tuple[float, float, float, float]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0 and block.get("bbox"):
            bboxes.append(tuple(block["bbox"]))
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect is None:
            continue
        if rect.width < 2 and rect.height < 2:
            continue
        bboxes.append((rect.x0, rect.y0, rect.x1, rect.y1))
    return bboxes


def _bbox_in_figure_band(
    bbox: tuple[float, float, float, float],
    caption_bbox: tuple[float, float, float, float],
    column: str,
    page_width: float,
    top: float,
    bottom: float,
) -> bool:
    if bbox[3] < top or bbox[1] > bottom:
        return False
    if column == "center":
        return True
    return _column_bucket(bbox, page_width) == column


def _figure_column_bounds(column: str, page_width: float) -> tuple[float, float]:
    if column == "left":
        return 36.0, page_width * 0.5 - 6.0
    if column == "right":
        return page_width * 0.5 + 6.0, page_width - 36.0
    return 36.0, page_width - 36.0


def _line_should_not_be_cropped_as_figure(text: str) -> bool:
    compact = text.strip()
    if not compact:
        return True
    if re.match(r"(?:Fig\.?|Figure)\s*\d+", compact, re.I):
        return True
    if re.match(r"(?:[A-Z]\.\s+[A-Z]|[IVX]+\.\s+[A-Z]|TABLE\s+[IVXLC0-9]+|REFERENCES$)", compact, re.I):
        return True
    if re.search(r"Authorized licensed use|Downloaded on|IEEE .*Conference", compact, re.I):
        return True
    return _looks_like_body_paragraph(compact)


def _select_table_caption(region_lines: list[dict], fallback: str, page_width: float) -> tuple[str, int]:
    for idx, line in enumerate(region_lines[1:], start=1):
        text = line["text"]
        if _looks_like_equation_line(text, line["bbox"], page_width):
            continue
        if _looks_like_table_data_line(text):
            continue
        if re.match(r"^\[\d+\]", text):
            continue
        if 2 <= len(text.split()) <= 12:
            return text, idx
    return fallback, 0


def _looks_like_table_data_line(text: str) -> bool:
    compact = text.strip()
    if re.search(r"\(\s*-?\d", compact):
        return True
    if len(re.findall(r"\d", compact)) >= 4 and len(compact.split()) <= 12:
        return True
    return False


def _looks_like_body_paragraph(text: str) -> bool:
    words = text.split()
    if len(words) < 10:
        return False
    lowercase_words = sum(1 for word in words if any(ch.islower() for ch in word))
    return lowercase_words >= max(4, len(words) // 2)


def _union_bboxes(bboxes: list[tuple[float, float, float, float]]) -> fitz.Rect:
    x0 = min(b[0] for b in bboxes)
    y0 = min(b[1] for b in bboxes)
    x1 = max(b[2] for b in bboxes)
    y1 = max(b[3] for b in bboxes)
    return fitz.Rect(x0, y0, x1, y1)


def _crop_region(page: fitz.Page, bbox: fitz.Rect, out_path: Path, rel_prefix: str) -> str:
    clip = fitz.Rect(bbox)
    clip.x0 = max(0, clip.x0 - 6)
    clip.y0 = max(0, clip.y0 - 6)
    clip.x1 = min(page.rect.width, clip.x1 + 6)
    clip.y1 = min(page.rect.height, clip.y1 + 6)
    if clip.is_empty or clip.width < 4 or clip.height < 4:
        return ""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False)
    pix.save(out_path)
    return f"{rel_prefix}/{out_path.name}".replace("\\", "/")


def _expand_table_crop_bbox(page: fitz.Page, bbox: fitz.Rect) -> fitz.Rect:
    """Expand visual table crops to include ruling lines and wide table edges."""
    region = fitz.Rect(bbox)
    x0 = region.x0
    x1 = region.x1
    vertical_pad = 8.0
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect is None:
            continue
        if rect.y1 < region.y0 - vertical_pad or rect.y0 > region.y1 + vertical_pad:
            continue
        if rect.x1 < region.x0 - 12.0 or rect.x0 > region.x1 + 12.0:
            continue
        if rect.width < max(24.0, region.width * 0.35) and rect.height < 2.0:
            continue
        x0 = min(x0, rect.x0)
        x1 = max(x1, rect.x1)
    expanded = fitz.Rect(x0, max(0.0, region.y0 - 2.0), x1, min(page.rect.height, region.y1 + 2.0))
    if expanded.width > page.rect.width * 0.62:
        expanded.x0 = min(expanded.x0, 24.0)
        expanded.x1 = max(expanded.x1, page.rect.width - 24.0)
    return expanded


def _table_crop_text(lines: list[dict], bbox: fitz.Rect) -> str:
    y0 = bbox.y0 - 4.0
    y1 = bbox.y1 + 4.0
    in_band: list[str] = []
    for line in lines:
        line_bbox = line["bbox"]
        if line_bbox[3] < y0 or line_bbox[1] > y1:
            continue
        text = line["text"].strip()
        if not text:
            continue
        if re.search(r"Authorized licensed use|Downloaded on|IEEE .*Conference", text, re.I):
            continue
        in_band.append(text)
    return _cleanup(" ".join(in_band))


def _merge_page_level_visuals(
    cpr: CanonicalPaperRepresentation,
    page_figures: list[Figure],
    page_tables: list[Table],
) -> CanonicalPaperRepresentation:
    if page_figures:
        by_label = {fig.label: fig for fig in cpr.figures}
        for page_fig in page_figures:
            existing = by_label.get(page_fig.label)
            if existing is None:
                cpr.figures.append(page_fig)
                by_label[page_fig.label] = page_fig
                continue
            if page_fig.caption and (
                len(page_fig.caption) < len(existing.caption)
                or existing.caption.lower().startswith("overview validation")
            ):
                existing.caption = page_fig.caption
            if page_fig.path and not existing.path:
                existing.path = page_fig.path
            existing.placement = "H"

    if page_tables:
        by_label = {table.label: table for table in cpr.tables}
        for page_table in page_tables:
            existing = by_label.get(page_table.label)
            if existing is None:
                cpr.tables.append(page_table)
                by_label[page_table.label] = page_table
                continue
            if page_table.caption and len(page_table.caption) < len(existing.caption):
                existing.caption = page_table.caption
            if page_table.latex and "% reconstructed table unavailable" in existing.latex:
                existing.latex = page_table.latex
    return cpr


def _guess_format(text: str) -> str:
    if "IEEE" in text[:5000]:
        return "ieee"
    if "ACM" in text[:5000]:
        return "acm"
    return "unknown"


# Boilerplate tokens that commonly appear on a paper's first page and must
# never be mistaken for a title. Matched case-insensitively.
_TITLE_BLOCKLIST_SUBSTRINGS = (
    "ieee infocom",
    "ieee xplore",
    "authorized licensed use",
    "downloaded on",
    "all rights reserved",
    "copyright",
    "acm reference format",
    "permission to make digital",
    "creative commons",
    "\u00a9",  # copyright symbol
    "doi:",
    "doi ",
    "proceedings of the",
    "conference on",
    "workshop on",
    "symposium on",
    "preprint",
    "arxiv:",
    "isbn",
    "issn",
)

# Lines that look like pure conference banner headers we should skip when
# searching for the title.
_CONF_BANNER_RE = re.compile(
    r"^(?:\d{4}\s+)?(?:IEEE|ACM|[0-9]{2}(?:st|nd|rd|th))\b.*(?:conference|symposium|workshop|congress)",
    re.I,
)

# Markers that end the frontmatter scan (title/author region).
_FRONTMATTER_END_RE = re.compile(r"abstract|index\s*terms|keywords|(?:i\.\s*|(?:\d+(?:\.\d+)?\.?\s*)?)introduction\b", re.I)
_AFFILIATION_LINE_HINTS = {
    "university", "department", "engineering", "science", "technology",
    "institute", "college", "school", "laboratory", "research", "center",
    "faculty", "campus", "academy",
}
_GEOGRAPHIC_TOKENS = {
    "north", "south", "east", "west", "usa", "us", "uk", "uae", "nc", "ny", "ca", "dc",
    "carolina", "louisiana", "washington",
}


def _extract_frontmatter(lines: list[str]) -> tuple[str, list[str], dict]:
    """Extract title, authors, and frontmatter metadata from cleaned PDF lines.

    Robustness priorities, in order:
    1. Skip conference banner lines, copyright lines, and DOI lines at the top
       of the first page. These commonly appear *before* the real title in
       IEEE/ACM PDFs and would otherwise be captured as the title.
    2. Gather up to two contiguous lines as the title, stopping as soon as we
       hit an author-like line (email, all-caps affiliation, short name line)
       or a frontmatter-end keyword.
    3. After the title block, scan a wider window for authors, emails, and
       affiliations, with generic keyword matching.
    """
    metadata: dict = {"emails": [], "affiliations": [], "author_profiles": []}
    title_lines: list[str] = []
    idx = 0

    # Step 1: skip banner/boilerplate until we see something that could be a title.
    scan_window = lines[:20]
    while idx < len(scan_window) and _is_title_banner_noise(scan_window[idx]):
        idx += 1

    # Step 2: collect up to two contiguous lines as the title.
    while idx < len(scan_window) and len(title_lines) < 2:
        line = scan_window[idx]
        if _FRONTMATTER_END_RE.search(line):
            break
        if "@" in line or re.search(r"\bdoi\b|\bdoi:\b", line, re.I):
            break
        if _is_title_banner_noise(line):
            idx += 1
            continue
        words = line.split()
        if 3 <= len(words) <= 25 and not line.isdigit() and len(line) <= 200:
            title_lines.append(line)
            idx += 1
            # If the next line is a direct continuation of the title (still
            # mostly title-case, no author markers), grab it too.
            if idx < len(scan_window):
                nxt = scan_window[idx]
                if (
                    not _FRONTMATTER_END_RE.search(nxt)
                    and "@" not in nxt
                    and 3 <= len(nxt.split()) <= 25
                    and not _looks_like_authors_line(nxt)
                    and not _is_title_banner_noise(nxt)
                ):
                    title_lines.append(nxt)
                    idx += 1
            break
        idx += 1

    title = " ".join(title_lines).strip()

    # Step 3: scan a wider window for authors, emails, and affiliations.
    frontmatter_lines: list[str] = []
    for line in lines[idx:idx + 40]:
        if _FRONTMATTER_END_RE.search(line):
            break
        frontmatter_lines.append(line)

    profiles = _extract_author_profiles(frontmatter_lines)
    authors: list[str] = [profile["name"] for profile in profiles if profile.get("name")]
    for profile in profiles:
        if profile.get("email"):
            metadata["emails"].append(profile["email"])
        affiliation = profile.get("institution") or profile.get("department")
        if affiliation:
            metadata["affiliations"].append(affiliation)
    if profiles:
        metadata["author_profiles"] = profiles

    for line in frontmatter_lines:
        if "@" in line:
            for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", line):
                metadata["emails"].append(email)
            continue
        if _looks_like_affiliation_line(line):
            metadata["affiliations"].append(line)
            continue
        if _looks_like_authors_line(line):
            # Split a "Author1, Author2, Author3" line into individual names.
            for token in re.split(r",|\band\b|\u2014|\u2013", line):
                token = token.strip()
                if token and len(token.split()) <= 5 and re.match(r"^[A-Z][A-Za-z .'\-]+$", token):
                    if token not in authors:
                        authors.append(token)
    metadata["emails"] = list(dict.fromkeys(metadata["emails"]))
    metadata["affiliations"] = list(dict.fromkeys(metadata["affiliations"]))
    return title, authors, metadata


def _extract_author_profiles(lines: list[str]) -> list[dict[str, str]]:
    """Recover per-author credential blocks from PDF frontmatter lines.

    Common IEEE PDFs emit each author as a compact vertical block:
    name, department/field, institution, location, email. Keeping those fields
    together prevents cities/countries from being mistaken for author names or
    attached to the wrong person in ACM output.
    """
    profiles: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line in lines:
        s = " ".join(line.split()).strip()
        if not s:
            continue
        if "@" in s:
            if current is not None:
                email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", s)
                if email:
                    current["email"] = email.group(0)
            continue
        if _looks_like_authors_line(s):
            if current and _profile_has_metadata(current):
                profiles.append(current)
            current = {"name": s}
            continue
        if current is None:
            continue
        if _looks_like_affiliation_line(s):
            if _looks_like_institution_line(s):
                current.setdefault("institution", s)
            else:
                current.setdefault("department", s)
            continue
        if _looks_like_location_line(s):
            current.update(_parse_location_line(s))

    if current and _profile_has_metadata(current):
        profiles.append(current)

    # Only trust this structured parse if it found real metadata for more than
    # one author or if the single-author block has an email/institution.
    if len(profiles) > 1:
        return profiles
    if len(profiles) == 1 and (profiles[0].get("email") or profiles[0].get("institution")):
        return profiles
    return []


def _profile_has_metadata(profile: dict[str, str]) -> bool:
    return any(profile.get(key) for key in ("email", "institution", "department", "city", "country"))


def _looks_like_institution_line(line: str) -> bool:
    lowered = line.strip().lower()
    institution_tokens = {
        "university", "institute", "college", "school", "laboratory",
        "research", "center", "faculty", "campus", "academy",
    }
    return any(token in lowered for token in institution_tokens)


def _is_title_banner_noise(line: str) -> bool:
    """Return True if a line is conference/licence/DOI noise masquerading as a title."""
    s = line.strip()
    if not s:
        return True
    low = s.lower()
    if any(token in low for token in _TITLE_BLOCKLIST_SUBSTRINGS):
        return True
    if _CONF_BANNER_RE.match(s):
        return True
    if re.match(r"^\d{3,4}$", s):
        return True
    if re.match(r"^(?:19|20)\d{2}\s+IEEE", s, re.I):
        return True
    if re.match(r"^978[-\d]+\s*\u00a9?\s*\d{4}\s+IEEE", s):
        return True
    return False


def _looks_like_authors_line(line: str) -> bool:
    """Return True if a line looks like a list of author names."""
    s = line.strip()
    if not s or len(s) > 200:
        return False
    if "@" in s or re.search(r"\d", s):
        return False
    if _looks_like_affiliation_line(s) or _looks_like_location_line(s):
        return False
    # A single short capitalised name line.
    words = [word for word in s.split() if word]
    if 2 <= len(words) <= 5 and all(_looks_like_person_name_token(word) for word in words):
        return True
    # A comma-separated list of capitalised names.
    parts = [p.strip() for p in re.split(r",|\band\b", s) if p.strip()]
    if len(parts) >= 2 and all(
        not _looks_like_location_line(p)
        and not _looks_like_affiliation_line(p)
        and 2 <= len(p.split()) <= 5
        and all(_looks_like_person_name_token(word) for word in p.split())
        for p in parts
    ):
        return True
    return False


def _looks_like_person_name_token(token: str) -> bool:
    stripped = token.strip().strip(",;:")
    if not stripped:
        return False
    if re.fullmatch(r"[A-Z]\.", stripped):
        return True
    return bool(re.fullmatch(r"[A-Z][A-Za-z'\-]*", stripped))


def _looks_like_affiliation_line(line: str) -> bool:
    lowered = line.strip().lower()
    return any(token in lowered for token in _AFFILIATION_LINE_HINTS)


def _looks_like_location_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _looks_like_comma_location(stripped):
        return True
    if stripped.lower() in {"usa", "us", "u.s.a.", "u.s.", "uk", "u.k.", "nc", "ny", "ca", "dc"}:
        return True
    words = [re.sub(r"[^A-Za-z]", "", word).lower() for word in stripped.split()]
    words = [word for word in words if word]
    if not words:
        return False
    if len(words) == 1:
        return True
    return all(word in _GEOGRAPHIC_TOKENS for word in words)


def _looks_like_comma_location(line: str) -> bool:
    if "," not in line or re.search(r"[@\d]", line):
        return False
    parts = [part.strip() for part in line.split(",") if part.strip()]
    if not 2 <= len(parts) <= 4:
        return False
    if any(_looks_like_affiliation_line(part) for part in parts):
        return False
    trailing = re.sub(r"[^A-Za-z.]", "", parts[-1]).lower().replace(".", "")
    if trailing not in {"usa", "us", "uk", "uae", "nc", "ny", "ca", "dc"} and trailing not in _GEOGRAPHIC_TOKENS:
        return False
    return all(1 <= len(part.split()) <= 3 for part in parts)


def _parse_location_line(line: str) -> dict[str, str]:
    stripped = " ".join(line.split()).strip()
    if "," in stripped:
        parts = [part.strip() for part in stripped.split(",") if part.strip()]
        if len(parts) >= 3:
            return {"city": parts[0], "state": parts[1], "country": parts[2]}
        if len(parts) == 2:
            second = parts[1]
            normalized = re.sub(r"[^A-Za-z.]", "", second).lower().replace(".", "")
            if normalized in {"usa", "us", "uk", "uae"}:
                return {"city": parts[0], "country": second}
            return {"city": parts[0], "state": second}
    normalized = re.sub(r"[^A-Za-z.]", "", stripped).lower().replace(".", "")
    if normalized in {"usa", "us", "uk", "uae"}:
        return {"country": stripped}
    if normalized in {"nc", "ny", "ca", "dc"}:
        return {"state": stripped}
    return {"city": stripped}


def _extract_abstract(text: str) -> str:
    intro_boundary = r"(?:^|\n)(?:I\.\s*INTRODUCTION|\d+(?:\.\d+)?\.?\s*INTRODUCTION|INTRODUCTION)\b"
    patterns = [
        rf"Abstract[\s—-]+(.*?)(?:Index Terms|Keywords|{intro_boundary})",
        rf"Abstract\s*(.*?)(?:Index Terms|Keywords|{intro_boundary})",
        rf"Abstract\s*(.*?)(?:{intro_boundary})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.S | re.I)
        if m:
            return _cleanup(m.group(1))
    return ""


def _extract_keywords(text: str) -> list[str]:
    intro_boundary = r"(?:^|\n)(?:I\.\s*INTRODUCTION|\d+(?:\.\d+)?\.?\s*INTRODUCTION|INTRODUCTION)\b"
    patterns = [
        rf"Index Terms[\s—-]+(.*?)(?:{intro_boundary})",
        rf"Keywords[\s—-]+(.*?)(?:{intro_boundary})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.S | re.I)
        if m:
            raw = _cleanup(m.group(1))
            return [k.strip() for k in re.split(r",|;", raw) if k.strip()]
    return []


def _extract_sections(text: str) -> list[Section]:
    raw_lines = text.splitlines()
    heading_lines = _find_section_heading_lines(raw_lines)
    sections: list[Section] = []
    for i, (line_index, heading_text) in enumerate(heading_lines):
        next_index = heading_lines[i + 1][0] if i + 1 < len(heading_lines) else len(raw_lines)
        content = _extract_section_content(raw_lines[line_index + 1:next_index])
        content = _repair_split_drop_cap(content)
        normalized_title = _normalize_heading(heading_text)
        min_length = 8 if normalized_title.lower() in {"references", "bibliography", "works cited"} else 40
        if len(content) < min_length:
            continue
        sections.append(Section(title=normalized_title, content=content))

    if not sections:
        return _fallback_sections(text)
    return sections


_ROMAN_SECTION_RE = re.compile(r"^[IVX]+\.\s+[A-Z][A-Z \-]+$")
_ARABIC_SECTION_RE = re.compile(r"^\d+(?:\.\d+)?\.?\s+[A-Z][A-Za-z][A-Za-z0-9'&/\-]*(?:\s+[A-Z][A-Za-z0-9'&/\-]*){0,6}$")
_UNNUMBERED_SECTION_RE = re.compile(r"^(?:[A-Z][A-Za-z0-9'&/\-]*|[A-Z]{2,})(?:\s+(?:[A-Z][A-Za-z0-9'&/\-]*|[A-Z]{2,})){0,4}$")
_UNNUMBERED_SECTION_BLOCKLIST = {
    "abstract",
    "index terms",
    "keywords",
    "actual points",
    "estimated points",
    "difference",
    "performance measurement",
}


def _find_section_heading_lines(lines: list[str]) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    nonempty_seen = 0
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        nonempty_seen += 1
        if _ROMAN_SECTION_RE.match(line) or _ARABIC_SECTION_RE.match(line):
            headings.append((idx, line))
            continue
        if _looks_like_unnumbered_section_heading(line, idx, lines, nonempty_seen):
            headings.append((idx, line))
    deduped: list[tuple[int, str]] = []
    seen_indexes: set[int] = set()
    for idx, title in headings:
        if idx in seen_indexes:
            continue
        seen_indexes.add(idx)
        deduped.append((idx, title))
    return deduped


def _looks_like_unnumbered_section_heading(line: str, idx: int, lines: list[str], nonempty_seen: int) -> bool:
    normalized = " ".join(line.split())
    lowered = normalized.lower()
    if lowered in _UNNUMBERED_SECTION_BLOCKLIST:
        return False
    if not _UNNUMBERED_SECTION_RE.match(normalized):
        return False
    if len(normalized.split()) == 1 and len(normalized) <= 3 and normalized.isupper():
        return False
    if normalized.endswith((".", ",", ";", ":")):
        return False
    next_line = _next_nonempty_line(lines, idx)
    if not next_line:
        return False
    if not (_looks_like_body_text_line(next_line) or _looks_like_reference_entry_lead(next_line) or _looks_like_subsection_line(next_line)):
        return False
    return True


def _next_nonempty_line(lines: list[str], idx: int) -> str:
    for candidate in lines[idx + 1:]:
        stripped = candidate.strip()
        if stripped:
            return stripped
    return ""


def _looks_like_body_text_line(text: str) -> bool:
    words = text.split()
    if len(words) < 6:
        return False
    lowercase_words = sum(1 for word in words if any(ch.islower() for ch in word))
    return lowercase_words >= max(3, len(words) // 3)


def _looks_like_reference_entry_lead(text: str) -> bool:
    return bool(
        re.match(r"^\[\d+\]", text)
        or re.match(r"^\d+[\.\)]\s+", text)
        or re.match(r"^(?:[A-Z]\.\s*){1,3}[A-Z][A-Za-z'-]+", text)
    )


def _looks_like_subsection_line(text: str) -> bool:
    return bool(re.match(r"^[A-Z]\.\s+[A-Z]", text))


def _extract_reference_placeholders(text: str) -> list[Reference]:
    references: list[Reference] = []
    seen: set[str] = set()
    ref_block = re.search(r"(?:^|\n)(?:REFERENCES|BIBLIOGRAPHY|WORKS\s+CITED)\s*(.*)$", text, re.I | re.S)
    if ref_block:
        trailing = ref_block.group(1)
        parts = re.split(r"(?=\[\d+\])", trailing)
        for part in parts:
            match = re.match(r"\[(\d+)\]\s*(.+)", part.strip(), re.S)
            if not match:
                continue
            key = f"ref{match.group(1)}"
            if key in seen:
                continue
            seen.add(key)
            raw = _cleanup(match.group(2))
            references.append(Reference(key=key, raw=raw or f"placeholder for {key}"))
    if references:
        return references
    for m in re.finditer(r"\[(\d+)\]", text):
        key = f"ref{m.group(1)}"
        if key in seen:
            continue
        seen.add(key)
        references.append(Reference(key=key, raw=f"placeholder for {key}"))
    return references


def _extract_reference_block(text: str) -> str:
    m = re.search(r"(?:^|\n)(?:REFERENCES|BIBLIOGRAPHY|WORKS\s+CITED)\s*(.*)$", text, re.I | re.S)
    return m.group(1).strip() if m else ""


def _try_structured_reference_parse(pdf_path: Path, text: str) -> tuple[list[Reference], str | None]:
    """Best-effort structured parser integration for PDF references.

    Priority:
    1. `anystyle` CLI (if installed)
    2. `grobid_client` CLI (if installed)

    Falls back silently when unavailable.
    """
    ref_block = _extract_reference_block(text)
    if not ref_block:
        return [], None

    refs = _try_anystyle_parse(ref_block)
    if refs:
        return refs, "anystyle"

    refs = _try_grobid_parse(pdf_path)
    if refs:
        return refs, "grobid_client"

    return [], None


def _try_anystyle_parse(ref_block: str) -> list[Reference]:
    exe = shutil.which("anystyle")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "parse", "--stdout"],
            input=ref_block,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        parsed = json.loads(proc.stdout)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    refs: list[Reference] = []
    for idx, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            continue
        raw_text = item.get("raw") or item.get("title")
        if isinstance(raw_text, list):
            raw_text = " ".join(str(x) for x in raw_text)
        raw_value = str(raw_text or "").strip()
        if not raw_value:
            continue
        refs.append(Reference(key=f"ref{idx}", raw=raw_value))
    return refs


def _try_grobid_parse(pdf_path: Path) -> list[Reference]:
    exe = shutil.which("grobid_client")
    if not exe:
        return []
    # Requires a running GROBID server + configured client; keep optional.
    try:
        proc = subprocess.run(
            [exe, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            return []
    except Exception:
        return []
    return []


def _strip_section_noise(content: str) -> str:
    content = re.sub(r"Authorized licensed use limited to:.*?Restrictions apply\.", " ", content, flags=re.I | re.S)
    content = re.sub(r"IEEE INFOCOM.*?Networks", " ", content, flags=re.I | re.S)
    content = re.sub(r"\b(?:Scan|Registration)\b\s+\d+(?:\s+\d+)*", " ", content)
    return _cleanup(content)


def _extract_section_content(lines: list[str]) -> str:
    block = _strip_section_noise_multiline("\n".join(lines))
    paragraphs: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if not current:
            return
        paragraph = _cleanup(" ".join(current))
        if paragraph:
            paragraphs.append(paragraph)
        current.clear()

    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if _should_skip_section_line(line):
            flush()
            continue
        if current and _starts_new_paragraph(current[-1], line):
            flush()
        current.append(line)

    flush()
    return "\n\n".join(paragraphs)


def _strip_section_noise_multiline(content: str) -> str:
    cleaned = content or ""
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("�", "-")
    cleaned = cleaned.replace("\u2019", "'")
    cleaned = re.sub(r"Authorized licensed use limited to:.*?Restrictions apply\.", " ", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"IEEE INFOCOM.*?Networks", " ", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"\b(?:Scan|Registration)\b\s+\d+(?:\s+\d+)*", " ", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _should_skip_section_line(line: str) -> bool:
    compact = line.strip()
    if not compact:
        return True
    if re.fullmatch(r"\d{1,3}", compact):
        return True
    if re.fullmatch(r"(?:19|20)\d{2}", compact):
        return True
    return False


def _starts_new_paragraph(previous_line: str, line: str) -> bool:
    if re.match(r"^(?:\d+\)|\(\d+\)|[A-Z]\.)\s+", line):
        return True
    if re.match(r"^\d+\.\d+$", previous_line) and line[:1].isupper():
        return True
    if re.match(r"^\d+\.\d+\s+", line):
        return True
    if previous_line.endswith((".", "!", "?")) and re.match(r"^(?:Fig\.|Figure|Table|TABLE)\s+\d+", line):
        return True
    return False


def _fallback_sections(text: str) -> list[Section]:
    chunks = [c.strip() for c in re.split(r"\n\n+", text) if c.strip()]
    if not chunks:
        return []
    body = " ".join(chunks[3:]) if len(chunks) > 3 else " ".join(chunks)
    return [Section(title="Body", content=_cleanup(body))]


def _normalize_heading(title: str) -> str:
    title = re.sub(r"^[IVX]+\.\s*", "", title).strip()
    title = re.sub(r"^\d+(?:\.\d+)?\.?\s*", "", title).strip()
    words = [w.capitalize() if w.isupper() else w.capitalize() for w in title.split()]
    return " ".join(words)


def _repair_split_drop_cap(text: str) -> str:
    repaired = re.sub(r"^([A-Z])\s+([A-Z]{2,}\b)", lambda m: m.group(1) + m.group(2), text.strip())
    return re.sub(
        r"^([A-Z]{3,})(?=\s+[a-z])",
        lambda m: m.group(1).capitalize(),
        repaired,
        count=1,
    )


def _cleanup(text: str) -> str:
    text = text.replace("�", "-")
    text = text.replace("\u2019", "'")
    text = text.replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()
