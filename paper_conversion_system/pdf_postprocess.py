from __future__ import annotations

import re
from .models import CanonicalPaperRepresentation, Section, Figure, Table, Reference


AFFILIATION_HINTS = [
    "university", "department", "engineering", "computer science",
    "school", "college", "institute", "laboratory", "research",
]
_AFFILIATION_HINTS_RE = re.compile(
    "|".join(re.escape(h) for h in AFFILIATION_HINTS), re.I
)
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
    """Turn bracket references into ``\\cite{key}`` LaTeX commands.

    Handles two citation styles found in academic PDFs:

    Numeric (IEEE/most conferences)::

        [1], [12], [1, 2, 3] → \\cite{ref1}, \\cite{ref12}, \\cite{ref1,ref2,ref3}

    Author-year (ACM / some venues)::

        [Smith et al. 2023] → \\cite{smith2023}

    Only converts citations whose keys are present in the CPR reference list.
    Never touches the References / Bibliography section itself.
    """
    if not cpr.references:
        return cpr
    known_keys = {ref.key for ref in cpr.references}
    _skip = {"references", "bibliography", "works cited"}

    def _replace_numeric(m: re.Match) -> str:
        # May be a list like [1, 2, 3] — convert each number individually
        nums = re.split(r"[,\s]+", m.group(1).strip())
        keys = []
        for n in nums:
            n = n.strip()
            if n.isdigit():
                k = f"ref{n}"
                if k in known_keys:
                    keys.append(k)
        if keys:
            return f"\\cite{{{','.join(keys)}}}"
        return m.group(0)  # no known key — leave bracket as-is

    def _replace_authoryear(m: re.Match) -> str:
        raw = m.group(1).strip()
        year_m = re.search(r"\b(19|20)\d{2}\b", raw)
        year = year_m.group(0) if year_m else ""
        first_word = re.split(r"[\s,]", raw)[0]
        last_name = re.sub(r"[^a-z]", "", first_word.lower())
        key = f"{last_name}{year}"
        if key in known_keys:
            return f"\\cite{{{key}}}"
        return m.group(0)

    # Determine which replacement strategy matches the reference list keys
    has_numeric_keys = any(re.match(r"ref\d+$", k) for k in known_keys)
    has_authoryear_keys = any(re.match(r"[a-z]+\d{4}$", k) for k in known_keys)

    updated: list[Section] = []
    for section in cpr.sections:
        if section.title.strip().lower() in _skip:
            updated.append(section)
            continue
        content = section.content
        if has_numeric_keys:
            content = re.sub(r"\[(\d+(?:[,\s]+\d+)*)\]", _replace_numeric, content)
        if has_authoryear_keys:
            content = re.sub(
                r"\[([A-Z][A-Za-z]+(?:\s+et\s+al\.?)?\s+(?:19|20)\d{2}[a-z]?)\]",
                _replace_authoryear,
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
        if not _AFFILIATION_HINTS_RE.search(lower):
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
    for section in cpr.sections:
        if section.title.lower() == "conclusion":
            match = re.search(r"(.*?)(?:REFERENCES\s+)(.*)$", section.content, flags=re.I | re.S)
            if match:
                updated_sections.append(Section(title=section.title, content=match.group(1).strip()))
                refs_text = match.group(2).strip()
                updated_sections.append(Section(title="References", content=refs_text))
                extracted_refs.extend(_parse_reference_entries(refs_text))
                continue
        if section.title.lower() == "references":
            extracted_refs.extend(_parse_reference_entries(section.content))
        updated_sections.append(section)
    cpr.sections = updated_sections
    if extracted_refs:
        dedup: dict[str, Reference] = {}
        for ref in extracted_refs:
            dedup[ref.key] = ref
        cpr.references = list(dedup.values())
    return cpr


def _extract_figures_and_tables(cpr: CanonicalPaperRepresentation) -> CanonicalPaperRepresentation:
    # If figures/tables were already extracted from the PDF binary (real images /
    # structured table data), keep them and don't overwrite with empty-path phantoms.
    has_real_figures = any(f.path for f in cpr.figures)
    has_real_tables = any("tabular" in t.latex for t in cpr.tables)

    figures: list[Figure] = list(cpr.figures) if has_real_figures else []
    tables: list[Table] = list(cpr.tables) if has_real_tables else []
    captions: list[str] = []

    for section in cpr.sections:
        if not has_real_figures:
            for m in re.finditer(r"(?:Fig\.|Figure)\s*(\d+)\.?\s*([^\n]{0,100})", section.content, flags=re.I):
                caption = (m.group(2) or "").strip(" .:-")
                lower = caption.lower()
                if not caption or len(caption) < 8:
                    continue
                if any(bad in lower for bad in BAD_FIGURE_PHRASES):
                    continue
                if len(caption.split()) < 4:
                    continue
                if not caption[0].isupper():
                    continue
                captions.append(m.group(0).strip())
                figures.append(Figure(label=f"fig:{m.group(1)}", caption=caption, path=""))

        if not has_real_tables:
            for m in re.finditer(r"TABLE\s+([IVXLC0-9]+)\s+([^\n]{0,120})", section.content, flags=re.I):
                caption = (m.group(2) or "").strip(" .:-")
                if not caption or len(caption) < 12:
                    continue
                if len(caption.split()) < 3:
                    continue
                captions.append(m.group(0).strip())
                tables.append(Table(label=f"tab:{m.group(1).lower()}", caption=caption, latex="% reconstructed table unavailable"))

    cpr.figures = figures[:6]
    cpr.tables = tables[:6]
    if captions:
        cpr.metadata["detected_captions"] = captions[:50]
    return cpr


def _clean_section_content(cpr: CanonicalPaperRepresentation) -> CanonicalPaperRepresentation:
    cleaned: list[Section] = []
    for section in cpr.sections:
        content = section.content
        # Remove figure/table references that were left in body text.
        content = re.sub(r"(?:Fig\.|Figure)\s*\d+\.?\s*[^\n]{0,120}", " ", content, flags=re.I)
        content = re.sub(r"TABLE\s+[IVXLC0-9]+\s*[^\n]{0,160}", " ", content, flags=re.I)
        content = re.sub(r"\b(?:Scan|Registration)\b\s+\d+(?:\s+\d+)*", " ", content)
        # Strip standalone page numbers that leaked from multi-column layout.
        # Targets exactly 3-digit numbers (100-999) that appear between prose
        # words, not inside citations/brackets and not followed by units or
        # percentage signs (avoids stripping "90 percent", years like "2016").
        content = re.sub(
            r"(?<![(\[.\-\d%])\s\b([1-9]\d{2})\b\s(?![,.\d%]|\bpercent\b|\bft\b|\bm\b|\bkm\b)",
            " ",
            content,
        )
        # Strip repeated all-caps table/result headers that leaked into body.
        # e.g. "FIRST RSSI VALUE RESULTS", "PERFORMANCE MEASUREMENT Actual…"
        content = re.sub(r"\b(?:FIRST|AVERAGE|MAXIMUM|MINIMUM)\s+(?:OF\s+\d+\s+)?[A-Z\s]{5,50}RESULTS\b", " ", content)
        content = re.sub(
            r"\bPERFORMANCE\s+MEASUREMENT\s+Actual.*?(?=\n|\Z)",
            " ", content, flags=re.S,
        )
        # Strip raw numeric table data rows: sequences of "(num, num) ... N ft".
        # These occur when table cell data leaks into the section body text.
        # Also matches partially-cleaned rows like "( 5 , ) (4.1 , 12.3) 1.96 ft".
        content = re.sub(
            r"\(\s*[\d.]*\s*,\s*[\d.]*\s*\)\s*\(\s*[\d.]*\s*,\s*[\d.]*\s*\)\s*[\d.]+\s*ft",
            " ",
            content,
        )
        # Strip "Actual points Estimated points Difference" table header lines.
        content = re.sub(r"\bActual\s+points?\s+Estimated\s+points?\s+Difference\b", " ", content, flags=re.I)
        content = re.sub(r"\s+", " ", content).strip()
        cleaned.append(Section(title=section.title, content=content))
    cpr.sections = cleaned
    return cpr


_PROSE_STARTER_RE = re.compile(
    # Words that start body sentences but never appear mid-title:
    # determiners, prepositions, conjunctions, auxiliary verbs, pronouns.
    # Deliberately excludes nouns that are valid in titles (measurement,
    # performance, procedure, experiment, etc.).
    r"\s+(?:The|This|These|Those|In|A|An|Of|Is|Are|Was|Were|We|Our|That|Which|"
    r"For|With|By|From|As|On|At|And|Or|But|If|When|Where|What|How|Who|Each|"
    r"consists?)\s",
    re.I,
)


def _trim_subsection_title(raw: str) -> str:
    """Remove body-text fragments that bleed into subsection titles.

    Subsection titles are short noun phrases (2-5 words). When PDF text
    extraction joins them with the following body sentence, common sentence-
    starting words (articles, prepositions) mark the boundary.
    """
    m = _PROSE_STARTER_RE.search(raw)
    if m:
        raw = raw[: m.start()].strip()
    # Hard cap: no real title needs more than 6 words / 60 chars.
    words = raw.split()
    return " ".join(words[:6]).strip()


def _add_subsection_markers(cpr: CanonicalPaperRepresentation) -> CanonicalPaperRepresentation:
    updated: list[Section] = []
    _SKIP_SECTIONS = {"references", "bibliography", "works cited", "acknowledgments", "acknowledgements"}

    for section in cpr.sections:
        # Never inject \subsection into reference lists — author initials like
        # "P. Grother" match the pattern and produce bogus subsections.
        if section.title.strip().lower() in _SKIP_SECTIONS:
            updated.append(section)
            continue

        def _replace_sub(m: re.Match) -> str:
            title = _trim_subsection_title(m.group(2).strip())
            if not title or len(title.split()) < 2:
                return m.group(0)  # single-word or empty — not a real subsection
            return f" \\subsection{{{title}}} "

        content = re.sub(
            r"(?<!\\subsection\{)(?:^|\s)([A-Z])\.\s+([A-Z][A-Za-z][A-Za-z'\- ]{2,80})(?=\s)",
            _replace_sub,
            section.content,
        )
        updated.append(Section(title=section.title, content=content))
    cpr.sections = updated
    return cpr


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
    """Parse a References section into Reference objects.

    Handles two citation-list styles:
    - IEEE numeric:   ``[1] A. Author, "Title," ...``
    - ACM author-year: ``Smith, A. et al. 2020. Title. ...`` (no leading bracket)
    """
    refs: list[Reference] = []

    # Try numeric style first (most common for IEEE / most conference papers)
    numeric_matches = list(re.finditer(r"\[(\d+)\]\s*(.*?)(?=\s*\[\d+\]|\Z)", text, flags=re.S))
    if numeric_matches:
        for m in numeric_matches:
            idx = m.group(1)
            raw = re.sub(r"\s+", " ", m.group(2)).strip()
            if len(raw) < 10:
                continue
            refs.append(Reference(key=f"ref{idx}", raw=raw))
        return refs

    # ACM / author-year style: entries separated by blank lines.
    # Split on double-newline boundaries and treat each paragraph as one entry.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for para in paragraphs:
        raw = re.sub(r"\s+", " ", para).strip()
        if len(raw) < 20:
            continue
        # Derive a key from the first author's last name + year.
        year_m = re.search(r"\b(19|20)\d{2}\b", raw)
        year = year_m.group(0) if year_m else ""
        first_word = re.split(r"[\s,]", raw)[0]
        last_name = re.sub(r"[^a-z]", "", first_word.lower())
        key = f"{last_name}{year}" if last_name and year else f"ref{len(refs)+1}"
        refs.append(Reference(key=key, raw=raw))
    return refs
