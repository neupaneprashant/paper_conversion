from __future__ import annotations

import re
from .models import CanonicalPaperRepresentation, Section, Figure, Table, Reference


AFFILIATION_HINTS = [
    "university", "department", "engineering", "computer science",
    "school", "college", "institute", "laboratory", "research",
]
# Generic signals that an extracted "Fig. N <text>" capture is not a real caption
# but rather body-prose carrying a figure reference. We avoid paper-specific phrasing.
BAD_FIGURE_PHRASES = [
    "as seen in",
    "as shown in",
    "shown in",
    "illustrated in",
    "depicted in",
    "presented in",
    "refer to",
    "see fig",
    "see figure",
    "see the",
    "using [",
    "this section",
    "which shows",
    "compared to",
    "according to",
    # Mid-sentence references like "...as in Fig. 3 we observe..." usually
    # produce a caption that starts with a lowercase word.
]


def refine_cpr_from_pdf(cpr: CanonicalPaperRepresentation) -> CanonicalPaperRepresentation:
    """Refine a raw PDF-derived CPR into something closer to renderable structure.

    This stage is deliberately post-cleanup and post-extraction. It exists because
    PDF text extraction usually produces a noisy but salvageable CPR that needs
    structural repair before April/Friday can make sensible template decisions.
    """
    cpr = _extract_frontmatter_from_sections(cpr)
    cpr = _separate_references(cpr)
    cpr = _extract_figures_and_tables(cpr)
    cpr = _clean_section_content(cpr)
    cpr = _convert_bracket_citations(cpr)
    cpr = _add_subsection_markers(cpr)
    cpr = _compute_ingest_confidence(cpr)
    return cpr


def _convert_bracket_citations(cpr: CanonicalPaperRepresentation) -> CanonicalPaperRepresentation:
    """Turn ``[12]`` style bracket references into ``\\cite{ref12}`` so bibtex
    sees real citations in the emitted LaTeX and the bibliography actually
    gets rendered. We only replace inside section content, not the References
    section itself, and only for keys that exist in the CPR reference list."""
    if not cpr.references:
        return cpr
    known_keys = {ref.key for ref in cpr.references}
    updated: list[Section] = []
    for section in cpr.sections:
        if section.title.strip().lower() in {"references", "bibliography", "works cited"}:
            updated.append(section)
            continue
        content = section.content
        content = re.sub(
            r"\[(\d+)\]",
            lambda m: f"\\cite{{ref{m.group(1)}}}" if f"ref{m.group(1)}" in known_keys else m.group(0),
            content,
        )
        updated.append(Section(title=section.title, content=content))
    cpr.sections = updated
    return cpr


def _extract_frontmatter_from_sections(cpr: CanonicalPaperRepresentation) -> CanonicalPaperRepresentation:
    """Refine author/email/affiliation metadata scraped from the first page.

    We prefer emails we can extract directly from the abstract/first-section
    text (those are cleaner than the raw frontmatter blob). We also cap the
    number of affiliations/emails at the number of authors so we don't emit
    more ``\\email{}`` lines than authors, which previously produced malformed
    ACM frontmatter. When we drop excess entries, we record a warning so the
    conversion report surfaces the lossy pairing to a reviewer.
    """
    source = cpr.abstract + "\n\n" + "\n\n".join([s.content for s in cpr.sections[:1]])
    scraped_emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", source)
    existing_emails = cpr.metadata.get("emails", []) or []
    all_emails = list(dict.fromkeys(list(existing_emails) + scraped_emails))

    raw_aff = cpr.metadata.get("affiliations", []) or []
    filtered_aff: list[str] = []
    for item in raw_aff:
        s = re.sub(r"\s+", " ", str(item)).strip()
        lower = s.lower()
        if len(s) > 120 or len(s) < 5:
            continue
        if not any(hint in lower for hint in AFFILIATION_HINTS):
            continue
        filtered_aff.append(s)
    filtered_aff = list(dict.fromkeys(filtered_aff))

    num_authors = max(1, len(cpr.authors))
    paired_aff = filtered_aff[:num_authors]
    paired_emails = all_emails[:num_authors]

    warnings = cpr.metadata.setdefault("warnings", [])
    if len(filtered_aff) > num_authors:
        warnings.append(
            f"Dropped {len(filtered_aff) - num_authors} extra affiliation(s) "
            f"to match {num_authors} author(s); review frontmatter."
        )
    if len(all_emails) > num_authors:
        warnings.append(
            f"Dropped {len(all_emails) - num_authors} extra email(s) "
            f"to match {num_authors} author(s); review frontmatter."
        )

    cpr.metadata["emails"] = paired_emails
    cpr.metadata["affiliations"] = paired_aff
    return cpr


def _separate_references(cpr: CanonicalPaperRepresentation) -> CanonicalPaperRepresentation:
    updated_sections: list[Section] = []
    extracted_refs: list[Reference] = []
    for index, section in enumerate(cpr.sections):
        title_lower = section.title.strip().lower()
        if title_lower in _REFERENCE_SECTION_TITLES:
            refs_text = _strip_reference_tail_noise(section.content)
            extracted_refs.extend(_parse_reference_entries(refs_text))
            updated_sections.append(Section(title="References", content=refs_text))
            continue

        body_text, refs_text = _split_embedded_reference_block(
            section.content,
            prefer_split=(title_lower in {"body", "conclusion"} or index == len(cpr.sections) - 1),
        )
        if refs_text:
            if body_text:
                updated_sections.append(Section(title=section.title, content=body_text))
            updated_sections.append(Section(title="References", content=refs_text))
            extracted_refs.extend(_parse_reference_entries(refs_text))
            continue

        updated_sections.append(section)
    cpr.sections = updated_sections
    if extracted_refs:
        dedup: dict[str, Reference] = {}
        for ref in extracted_refs:
            dedup[ref.key] = ref
        cpr.references = list(dedup.values())
    return cpr


_REFERENCE_SECTION_TITLES = {"references", "bibliography", "works cited"}
_REFERENCE_MARKER_RE = re.compile(r"\b(?:REFERENCES|References|BIBLIOGRAPHY|Bibliography|WORKS\s+CITED|Works\s+Cited)\b")


def _split_embedded_reference_block(content: str, prefer_split: bool = False) -> tuple[str, str]:
    match = _REFERENCE_MARKER_RE.search(content or "")
    if not match:
        return content, ""
    tail = _strip_reference_tail_noise((content or "")[match.end():].strip())
    if not _looks_like_reference_block(tail):
        return content, ""
    body = (content or "")[:match.start()].strip()
    if not prefer_split and len(body.split()) < 25:
        return content, ""
    return body, tail


def _looks_like_reference_block(text: str) -> bool:
    if not text or len(text) < 20:
        return False
    parsed = _parse_reference_entries(text)
    if len(parsed) >= 2:
        return True
    if len(parsed) == 1:
        raw = parsed[0].raw
        return bool(re.search(r"\b(19|20)\d{2}\b", raw) and len(raw.split()) >= 4)
    return False


def _strip_reference_tail_noise(text: str) -> str:
    cleaned = text or ""
    cleaned = re.sub(r"LIST OF FIGURES.*", "", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"LIST OF TABLES.*", "", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"APPENDI(?:X|CES).*", "", cleaned, flags=re.I | re.S)
    return cleaned.strip()


def _extract_figures_and_tables(cpr: CanonicalPaperRepresentation) -> CanonicalPaperRepresentation:
    figures: list[Figure] = []
    tables: list[Table] = []
    captions: list[str] = []
    figure_section_map: dict[str, str] = {}
    table_section_map: dict[str, str] = {}
    for section in cpr.sections:
        for m in re.finditer(r"(?:Fig\.|Figure)\s*(\d+)\.?\s*([^\n]{0,100})", section.content, flags=re.I):
            caption = (m.group(2) or "").strip(" .:-")
            lower = caption.lower()
            if not caption or len(caption) < 8:
                continue
            if any(bad in lower for bad in BAD_FIGURE_PHRASES):
                continue
            if len(caption.split()) < 4:
                continue
            # Real figure captions start with a capitalised word.
            # Fragments that start lowercase are almost always body prose
            # continuing from the "Fig. N" reference.
            if not caption[0].isupper():
                continue
            captions.append(m.group(0).strip())
            label = f"fig:{m.group(1)}"
            figures.append(Figure(label=label, caption=caption, path=""))
            figure_section_map[label] = section.title
        for m in re.finditer(r"TABLE\s+([IVXLC0-9]+)\s+([^\n]{0,120})", section.content, flags=re.I):
            caption = (m.group(2) or "").strip(" .:-")
            if not caption or len(caption) < 12:
                continue
            if len(caption.split()) < 3:
                continue
            captions.append(m.group(0).strip())
            label = f"tab:{m.group(1).lower()}"
            tables.append(Table(label=label, caption=caption, latex="% reconstructed table unavailable"))
            table_section_map[label] = section.title
    cpr.figures = figures[:4]
    cpr.tables = tables[:3]
    if captions:
        cpr.metadata["detected_captions"] = captions[:50]
    if figure_section_map:
        cpr.metadata["figure_section_map"] = figure_section_map
    if table_section_map:
        cpr.metadata["table_section_map"] = table_section_map
    return cpr


def _clean_section_content(cpr: CanonicalPaperRepresentation) -> CanonicalPaperRepresentation:
    cleaned: list[Section] = []
    for section in cpr.sections:
        content = section.content
        content = re.sub(r"(?:Fig\.|Figure)\s*\d+\.?\s*[^\n]{0,120}", " ", content, flags=re.I)
        content = re.sub(r"TABLE\s+[IVXLC0-9]+\s*[^\n]{0,160}", " ", content, flags=re.I)
        content = re.sub(r"\b(?:Scan|Registration)\b\s+\d+(?:\s+\d+)*", " ", content)
        content = re.sub(r"\s+", " ", content).strip()
        cleaned.append(Section(title=section.title, content=content))
    cpr.sections = cleaned
    return cpr


def _add_subsection_markers(cpr: CanonicalPaperRepresentation) -> CanonicalPaperRepresentation:
    updated: list[Section] = []
    for section in cpr.sections:
        content = re.sub(
            r"(?<!\\subsection\{)(^|\s)([A-Z])\.\s+([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,7})",
            lambda m: _replace_subsection_candidate(m.group(1), m.group(3)),
            section.content,
        )
        updated.append(Section(title=section.title, content=content))
    cpr.sections = updated
    return cpr


_SUBSECTION_BODY_STARTS = {"In", "The", "This", "These", "Where", "When", "Then", "After", "Before", "If", "For", "To"}


def _replace_subsection_candidate(prefix: str, candidate: str) -> str:
    title, remainder = _split_subsection_candidate(candidate)
    if not title:
        return f"{prefix}{candidate}"
    suffix = f" {remainder}" if remainder else ""
    return f"{prefix}\\subsection{{{title}}}{suffix}"


def _split_subsection_candidate(candidate: str) -> tuple[str, str]:
    words = [word.strip() for word in candidate.split() if word.strip()]
    kept: list[str] = []
    for index, word in enumerate(words):
        bare = word.strip(",.;:")
        if kept and (bare in _SUBSECTION_BODY_STARTS or re.fullmatch(r"\d+\)", bare)):
            return " ".join(kept).strip(), " ".join(words[index:]).strip()
        kept.append(bare)
        if len(kept) >= 4 and index + 1 < len(words):
            return " ".join(kept).strip(), " ".join(words[index + 1:]).strip()
    if len(kept) < 2:
        return "", candidate.strip()
    return " ".join(kept).strip(), ""


def _compute_ingest_confidence(cpr: CanonicalPaperRepresentation) -> CanonicalPaperRepresentation:
    score = 0.0
    if cpr.title and len(cpr.title.split()) > 3:
        score += 0.2
    if cpr.abstract and len(cpr.abstract) > 200:
        score += 0.2
    if len(cpr.sections) >= 4:
        score += 0.2
    if cpr.keywords:
        score += 0.1
    if cpr.authors:
        score += 0.1
    if cpr.references:
        score += 0.1
    if cpr.metadata.get("emails") or cpr.metadata.get("affiliations"):
        score += 0.1
    cpr.metadata["ingest_confidence"] = round(score, 2)
    return cpr


def _parse_reference_entries(text: str) -> list[Reference]:
    refs: list[Reference] = []
    matches = list(re.finditer(r"\[(\d+)\]\s*(.*?)(?=\s*\[\d+\]|$)", text, flags=re.S))
    if not matches:
        matches = list(re.finditer(r"(?:^|\s)(\d+)[\.\)]\s*(.*?)(?=(?:\s+\d+[\.\)])|$)", text, flags=re.S))
    for m in matches:
        idx = m.group(1)
        raw = re.sub(r"\s+", " ", m.group(2)).strip()
        if len(raw) < 10:
            continue
        refs.append(Reference(key=f"ref{idx}", raw=raw))
    return refs
