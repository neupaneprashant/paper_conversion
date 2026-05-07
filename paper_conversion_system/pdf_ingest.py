from __future__ import annotations

from pathlib import Path
import re
import fitz

from .models import CanonicalPaperRepresentation, Section, Reference, Figure, Table
from .pdf_cleanup import clean_pdf_text, aggressive_cleanup_pass
from .pdf_doctype import detect_pdf_document_type, extract_thesis_body_text
from .pdf_postprocess import refine_cpr_from_pdf
from .pdf_thesis import parse_thesis_text_to_cpr


def extract_figures_from_pdf(pdf_path: Path, out_dir: Path | None = None) -> list[Figure]:
    """Extract embedded images from a PDF and return Figure objects.

    Uses PyMuPDF to pull raster images directly from the PDF's xref table,
    then attempts to pair each image with the nearest caption text found on
    the same page.  Images are saved to out_dir/figures/ (or alongside the
    PDF if out_dir is None).
    """
    save_dir = (out_dir or pdf_path.parent) / "figures"
    save_dir.mkdir(parents=True, exist_ok=True)

    figures: list[Figure] = []
    seen_xrefs: set[int] = set()

    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return []

    # Build a page-text map for caption pairing.
    page_texts: list[str] = [page.get_text() for page in doc]

    fig_counter = 0
    for page_num, page in enumerate(doc):
        page_text = page_texts[page_num]
        img_list = page.get_images(full=True)
        for img_info in img_list:
            xref = img_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            try:
                img_data = doc.extract_image(xref)
            except Exception:
                continue

            # Skip tiny images (icons, bullets, decorative rules).
            w, h = img_data.get("width", 0), img_data.get("height", 0)
            if w < 80 or h < 80:
                continue

            ext = img_data.get("ext", "png")
            fig_counter += 1
            filename = f"fig{fig_counter:02d}.{ext}"
            img_path = save_dir / filename

            try:
                img_path.write_bytes(img_data["image"])
            except Exception:
                continue

            caption = _find_caption_near_figure(page_text, fig_counter)
            label = f"fig{fig_counter}"
            figures.append(Figure(
                label=label,
                caption=caption,
                path=f"figures/{filename}",
                placement="tbp",
            ))

    return figures


_CAPTION_RE = re.compile(
    r"(?:Fig(?:ure)?\.?\s*\d+[.:)\s]|TABLE\s+[IVX]+[.:)\s])(.*?)(?=\n\n|\Z)",
    re.I | re.S,
)


def _find_caption_near_figure(page_text: str, fig_num: int) -> str:
    """Extract the caption for figure `fig_num` from page text.

    Strategy 1 (preferred): find "Fig. N." or "Figure N." at the START of a
    line — this is the formal caption heading, not an in-text reference.
    Strategy 2: any in-text reference, but capped at 150 chars to avoid
    capturing body prose.
    """
    # Strategy 1: standalone caption at start of line (most reliable).
    # Matches "Fig. 1. Caption text." or "Figure 1: Caption text." at a line start.
    line_start_pat = re.compile(
        rf"(?:^|\n)\s*Fig(?:ure)?\.?\s*{fig_num}[.:\s]{{1,3}}(.{{0,200}})(?:\n|\Z)",
        re.I,
    )
    m = line_start_pat.search(page_text)
    if m:
        raw = re.sub(r"\s*\n\s*", " ", m.group(1)).strip().rstrip(" .")
        if len(raw) >= 6:
            return _cleanup(raw)

    # Strategy 2: any reference, capped at 150 chars (avoids body-text capture).
    inline_pat = re.compile(
        rf"Fig(?:ure)?\.?\s*{fig_num}[.:\s)(]\s*(.{{6,150}}?)(?:\.(?:\s|$)|\n|\Z)",
        re.I,
    )
    m2 = inline_pat.search(page_text)
    if m2:
        return _cleanup(m2.group(1))

    return f"Figure {fig_num}"


def extract_tables_from_pdf(pdf_path: Path) -> list[Table]:
    """Extract tables from a PDF using PyMuPDF find_tables() and convert to LaTeX tabular."""
    tables: list[Table] = []
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return []

    tab_counter = 0
    for page in doc:
        page_text = page.get_text()
        try:
            found = page.find_tables()
        except Exception:
            continue
        for tab in found.tables:
            data = tab.extract()
            if not data or len(data) < 2:
                continue
            if max(len(row) for row in data) < 2:
                continue
            tab_counter += 1
            label = f"tab{tab_counter}"
            caption = _find_table_caption(page_text, tab_counter)
            latex = _table_data_to_latex(data)
            tables.append(Table(label=label, caption=caption, latex=latex))
    return tables


def _find_table_caption(page_text: str, tab_num: int) -> str:
    """Find the caption for table tab_num in page_text.

    Strategy 1 (preferred): look for a short, title-style string immediately
    after "TABLE N" on the same line — e.g. "TABLE I FIRST RSSI VALUE RESULTS".
    These are the real table titles; they are ALL-CAPS or Title Case and do not
    start with verbs like "shows"/"presents".

    Strategy 2 (fallback): grab the longer prose description that appears when
    the body text says "TABLE I shows the estimated positions…".
    """
    roman = _to_roman(tab_num)
    # Strategy 1: title on the same line as "TABLE N" (common in IEEE papers).
    title_pat = re.compile(
        rf"TABLE\s+(?:{roman}|{tab_num})\s+([A-Z][A-Z0-9 ()]{{3,70}})(?:\n|\Z)",
    )
    m_title = title_pat.search(page_text)
    if m_title:
        raw = m_title.group(1).strip().rstrip(" .")
        # Reject if it starts with a prose verb (then it's a body-text reference).
        if not re.match(r"(?:shows?|presents?|illustrates?|depicts?|lists?|contains?)\b", raw, re.I):
            return _cleanup(raw)

    # Strategy 2: longer descriptive caption in body prose.
    m = re.search(
        rf"TABLE\s+(?:{roman}|{tab_num})[.:\s](.{{0,300}})(?=TABLE\s+[IVXLC\d]|\Z)",
        page_text, re.I | re.S,
    )
    if m:
        raw = m.group(1)
        caption = re.sub(r"\s*\n\s*", " ", raw).strip().rstrip(" .")
        return _cleanup(caption)

    m2 = re.search(r"TABLE\s+[IVXLC\d]+[.:\s](.{0,300})(?=TABLE|\Z)", page_text, re.I | re.S)
    if m2:
        raw = m2.group(1)
        return _cleanup(re.sub(r"\s*\n\s*", " ", raw).strip())

    return f"Table {tab_num}"


def _to_roman(n: int) -> str:
    vals = [(1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),
            (50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]
    r = ""
    for v, s in vals:
        while n >= v:
            r += s; n -= v
    return r


def _table_data_to_latex(data: list[list]) -> str:
    rows = [[str(c).strip() if c is not None else "" for c in row] for row in data]
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    col_spec = "l" + "c" * (ncols - 1)
    lines = [f"\\begin{{tabular}}{{{col_spec}}}", "\\toprule"]
    lines.append(" & ".join(f"\\textbf{{{_escape_cell(c)}}}" for c in rows[0]) + " \\\\")
    lines.append("\\midrule")
    for row in rows[1:]:
        lines.append(" & ".join(_escape_cell(c) for c in row) + " \\\\")
    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    return "\n".join(lines)


def _escape_cell(text: str) -> str:
    text = text.replace("\\", "\\textbackslash{}")
    for ch in "&%$#_{}~^":
        text = text.replace(ch, "\\" + ch)
    return text


# Greek and math Unicode that signals a display equation line.
_MATH_CHARS_RE = re.compile(
    r"[\u0391-\u03C9\u2200-\u22FF\u00B2\u00B3\u00B9\u221A\u00D7\u00F7\u2264\u2265\u2260]"
)
_EQ_NUMBER_RE = re.compile(r"\(\d+\)\s*$")


def extract_equations_from_pdf(pdf_path: Path, out_dir: Path | None = None) -> list[str]:
    """Extract display equations from a PDF as LaTeX strings or rendered image snippets."""
    equations: list[str] = []
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return []

    save_dir = (out_dir or pdf_path.parent) / "figures"
    save_dir.mkdir(parents=True, exist_ok=True)
    eq_counter = 0

    from collections import defaultdict

    for page_num, page in enumerate(doc):
        words = page.get_text("words")
        page_rect = page.rect

        lines_map: dict[tuple, list] = defaultdict(list)
        for w in words:
            lines_map[(w[5], w[6])].append(w)

        for key in sorted(lines_map):
            line_words = sorted(lines_map[key], key=lambda w: w[0])
            line_text = " ".join(w[4] for w in line_words).strip()

            if not _is_display_equation(line_text, page_rect, line_words):
                continue

            eq_counter += 1
            latex = _unicode_to_latex_equation(line_text)
            if latex:
                equations.append(latex)
                continue

            # Fallback: render the region as a PNG.
            x0 = min(w[0] for w in line_words) - 10
            y0 = min(w[1] for w in line_words) - 8
            x1 = max(w[2] for w in line_words) + 10
            y1 = max(w[3] for w in line_words) + 8
            rect = fitz.Rect(
                max(0, x0), max(0, y0),
                min(page_rect.width, x1), min(page_rect.height, y1),
            )
            try:
                pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=rect)
                eq_file = save_dir / f"eq{eq_counter:02d}.png"
                pix.save(str(eq_file))
                equations.append(
                    f"\\[\n"
                    f"  \\includegraphics[width=0.6\\linewidth]{{figures/eq{eq_counter:02d}.png}}\n"
                    f"\\]"
                )
            except Exception:
                pass

    return equations


_PROSE_STARTERS_RE = re.compile(
    r"^(?:the|a|an|this|that|these|those|where|which|when|is|are|was|were|"
    r"we|our|in|for|with|as|by|from|on|at|and|or|but|if|since|thus|so|"
    r"however|therefore|hence|let|given|note|fig|table)\b",
    re.I,
)


def _is_display_equation(text: str, page_rect, line_words) -> bool:
    if len(text) > 140 or len(text) < 3:
        return False
    # Must have at least one math symbol or end with an equation number.
    math_hits = _MATH_CHARS_RE.findall(text)
    has_eq_num = bool(_EQ_NUMBER_RE.search(text))
    if not math_hits and not has_eq_num:
        return False
    # Reject prose sentences that happen to contain a Greek letter.
    if _PROSE_STARTERS_RE.match(text.strip()):
        return False
    # Require at least 2 math symbols unless it ends with an equation label.
    if len(math_hits) < 2 and not has_eq_num:
        return False
    if not line_words:
        return False
    line_width = max(w[2] for w in line_words) - min(w[0] for w in line_words)
    if line_width > 0.75 * page_rect.width:
        return False
    return True


_UNICODE_MATH = [
    ("\u03B3", r"\gamma"), ("\u03B1", r"\alpha"), ("\u03B2", r"\beta"),
    ("\u03C3", r"\sigma"), ("\u03C9", r"\omega"), ("\u03B4", r"\delta"),
    ("\u03B5", r"\varepsilon"), ("\u03BB", r"\lambda"), ("\u03BC", r"\mu"),
    ("\u03C0", r"\pi"), ("\u03C1", r"\rho"), ("\u03B8", r"\theta"),
    ("\u221A", r"\sqrt"), ("\u00D7", r"\times"), ("\u00F7", r"\div"),
    ("\u2264", r"\leq"), ("\u2265", r"\geq"), ("\u2260", r"\neq"),
    ("\u2208", r"\in"), ("\u2211", r"\sum"), ("\u220F", r"\prod"),
    ("\u222B", r"\int"), ("\u221E", r"\infty"), ("\u2207", r"\nabla"),
    ("\u00B2", "^{2}"), ("\u00B3", "^{3}"), ("\u00B9", "^{1}"),
    ("\u2081", "_{1}"), ("\u2080", "_{0}"),
    ("\u2192", r"\rightarrow"), ("\u21D2", r"\Rightarrow"),
]


def _unicode_to_latex_equation(text: str) -> str:
    """Convert a line of Unicode math to a LaTeX equation block."""
    stripped = _EQ_NUMBER_RE.sub("", text).strip()
    result = stripped
    for uni, latex in _UNICODE_MATH:
        result = result.replace(uni, latex)
    if result == stripped:
        return ""  # No conversions made — not worth emitting.
    return f"\\begin{{equation}}\n{result}\n\\end{{equation}}"


def parse_pdf_to_cpr(pdf_path: Path, source_format_hint: str | None = None, cleanup_mode: str = "safe") -> CanonicalPaperRepresentation:
    """Parse a PDF into CPR.

    This function is the main PDF ingestion entrypoint. It detects document
    type, routes thesis/dissertation PDFs to the thesis-specific parser,
    otherwise applies PDF cleanup, extracts CPR fields, refines the CPR,
    and optionally attempts an aggressive cleanup pass when confidence is high.
    """
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        return CanonicalPaperRepresentation(
            title=pdf_path.stem,
            authors=[], abstract="", keywords=[], sections=[],
            figures=[], tables=[], equations=[], references=[],
            metadata={"error": f"PDF open failed: {exc}", "ingest_confidence": 0.0,
                      "source_path": str(pdf_path), "ingest_mode": "pdf"},
        )
    pages = [page.get_text() for page in doc]
    raw_text = "\n\n".join(pages)
    doc_type, doc_meta = detect_pdf_document_type(raw_text)
    if doc_type == "thesis_dissertation":
        cpr = parse_thesis_text_to_cpr(raw_text, source_path=str(pdf_path))
        cpr.metadata["document_type_meta"] = doc_meta
        cpr.metadata["page_count"] = len(doc)
        return cpr

    text_source = raw_text
    text, cleanup_meta = clean_pdf_text(text_source, mode=cleanup_mode)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title, authors, metadata = _extract_frontmatter(lines)
    abstract = _extract_abstract(text)
    keywords = _extract_keywords(text)
    sections = _extract_sections(text)
    references = _extract_reference_placeholders(text)

    metadata.update({
        "source_format": source_format_hint or _guess_format(text),
        "source_path": str(pdf_path),
        "ingest_mode": "pdf",
        "page_count": len(doc),
        "cleanup": cleanup_meta,
        "document_type": doc_type,
        "document_type_meta": doc_meta,
    })

    # Attempt aggressive cleanup before building CPR so that refine_cpr_from_pdf
    # (which runs _separate_references) sees the final section list and doesn't
    # get overwritten afterwards.
    if cleanup_mode == "safe":
        aggressive_text = aggressive_cleanup_pass(text)
        aggressive_sections = _extract_sections(aggressive_text)
        if len(aggressive_sections) >= max(3, len(sections) - 1):
            sections = aggressive_sections
            cleanup_meta["aggressive_applied"] = True
        else:
            cleanup_meta["aggressive_applied"] = False
    else:
        cleanup_meta["aggressive_applied"] = False

    figures = extract_figures_from_pdf(pdf_path, out_dir=pdf_path.parent)
    tables = extract_tables_from_pdf(pdf_path)
    equations = extract_equations_from_pdf(pdf_path, out_dir=pdf_path.parent)

    cpr = CanonicalPaperRepresentation(
        title=title or pdf_path.stem,
        authors=authors,
        abstract=abstract,
        keywords=keywords,
        sections=sections,
        figures=figures,
        tables=tables,
        equations=equations,
        references=references,
        metadata=metadata,
    )
    cpr = refine_cpr_from_pdf(cpr)

    if doc_type == "thesis_dissertation":
        cpr.metadata.setdefault("warnings", []).append(
            "Detected thesis/dissertation-style PDF; using alternate body-focused parsing stub. Output quality may be lower than paper-native parsing."
        )

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
_FRONTMATTER_END_RE = re.compile(r"abstract|index\s*terms|keywords|i\.\s*introduction", re.I)


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
    metadata: dict = {"emails": [], "affiliations": []}
    title_lines: list[str] = []
    idx = 0

    # Step 1: skip banner/boilerplate until we see something that could be a title.
    scan_window = lines[:20]
    while idx < len(scan_window) and _is_title_banner_noise(scan_window[idx]):
        idx += 1

    # Step 2: collect up to four contiguous lines as the title.
    # Academic titles often span 2-4 lines in PDF text extraction.
    while idx < len(scan_window):
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
            if _looks_like_authors_line(line) and title_lines:
                break  # We've hit an author line — title collection is done.
            title_lines.append(line)
            idx += 1
            if len(title_lines) >= 4:
                break  # Hard cap: 4 lines is enough for any reasonable title.
        else:
            if title_lines:
                break  # Non-title line after we started collecting — stop.
            idx += 1

    title = " ".join(title_lines).strip()

    # Step 3: scan a wider window for authors, emails, and affiliations.
    # Track lines already consumed as affiliations/emails so they are not
    # also added to the authors list (affiliation lines can pass the
    # _looks_like_authors_line heuristic when they start with a capital word).
    authors: list[str] = []
    classified: set[str] = set()
    for line in lines[idx:idx + 30]:
        if _FRONTMATTER_END_RE.search(line):
            break
        if "@" in line:
            for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", line):
                metadata["emails"].append(email)
            classified.add(line.lower())
            continue
        _AFFIL_TOKENS = (
            "university", "department", "engineering", "science", "institute",
            "college", "school", "laboratory", "research center", "lab,", "lab ",
            "technology", "polytechnic", "faculty",
        )
        _LOCATION_TOKENS = (
            "north carolina", "south carolina", "louisiana", "california",
            "virginia", "carolina", "greensboro", "wilmington", "ruston",
            " usa", ", usa", "united states", " uk", ", uk", "united kingdom",
        )
        line_lo = line.lower()
        if any(token in line_lo for token in _AFFIL_TOKENS + _LOCATION_TOKENS):
            metadata["affiliations"].append(line)
            classified.add(line_lo)
            continue
        # Reject single-word all-caps location codes (NC, USA, LA, etc.).
        if _ABBREV_STATE_RE.match(line.strip()):
            classified.add(line.lower())
            continue
        if line.lower() in classified:
            continue
        if _looks_like_authors_line(line):
            # Split a "Author1, Author2, Author3" line into individual names.
            for token in re.split(r",|\band\b|\u2014|\u2013", line):
                token = token.strip()
                if token and len(token.split()) <= 5 and re.match(r"^[A-Z][A-Za-z .'\-]+$", token):
                    authors.append(token)
    return title, authors, metadata


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


_LOCATION_ONLY_RE = re.compile(
    r"^(?:[A-Z]{2,3}|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)$"  # "USA", "NC", "Greensboro", "North Carolina"
)
_ABBREV_STATE_RE = re.compile(r"^[A-Z]{2,3}$")  # "NC", "USA", "LA"


def _looks_like_authors_line(line: str) -> bool:
    """Return True if a line looks like a list of human author names.

    Key invariants:
    - Author names require >= 2 words (avoids single-word city/state names).
    - All words must start with an uppercase letter (avoids title continuations
      like "User's Wi-Fi-enabled devices" where lowercase words appear).
    - Common city/state/country abbreviations are rejected.
    """
    s = line.strip()
    if not s or len(s) > 200:
        return False
    words = s.split()
    # Need at least 2 words — single words are almost never author names.
    if len(words) < 2:
        return False
    # All content words must start with an uppercase letter.
    # (Initials like "S." and honorifics are uppercase, lowercase words are prose.)
    if not all(w[0].isupper() for w in words if re.match(r"[A-Za-z]", w)):
        return False
    # Reject all-caps abbreviations that look like state/country codes.
    if _ABBREV_STATE_RE.match(s) or all(_ABBREV_STATE_RE.match(w) for w in words):
        return False
    # A short capitalised multi-word line — could be name or affiliation start.
    if len(words) <= 6 and re.match(r"^[A-Z][A-Za-z .'\-]+$", s):
        return True
    # A comma-separated list of capitalised names.
    parts = [p.strip() for p in re.split(r",|\band\b", s) if p.strip()]
    if len(parts) >= 2 and all(re.match(r"^[A-Z][A-Za-z .'\-]{1,40}$", p) for p in parts):
        return True
    return False


def _extract_abstract(text: str) -> str:
    _SECTION_INTRO = r"(?:I\.?\s*INTRODUCTION|1\.\s*INTRODUCTION|INTRODUCTION)"
    patterns = [
        rf"Abstract[\s\u2014\-]+(.*?)(?:Index Terms|Keywords|{_SECTION_INTRO})",
        rf"Abstract\s*(.*?)(?:Index Terms|Keywords|{_SECTION_INTRO})",
        # Fallback: next major numbered section or double blank line
        r"Abstract\s*(.*?)(?:\n\n\n|\n(?=[IVX]+\.\s+[A-Z])|$)",
        # Last resort: grab text between "Abstract" and the next all-caps heading
        r"Abstract\s*(.*?)(?:\n[A-Z][A-Z\s]{3,}(?:\n|$))",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.S | re.I)
        if m:
            result = _cleanup(m.group(1))
            if len(result) > 30:
                return result
    return ""


def _extract_keywords(text: str) -> list[str]:
    # Match section terminator for both IEEE (Roman numeral) and ACM (Arabic numeral) styles
    _intro_terminator = r"(?:I\.\s*INTRODUCTION|1\.?\s*INTRODUCTION|INTRODUCTION\s*\n)"
    patterns = [
        rf"Index Terms[\s—\-]+(.*?)(?:{_intro_terminator})",
        rf"Keywords[\s—\-]+(.*?)(?:{_intro_terminator})",
        # Fallback: keywords followed by two newlines (ACM \keywords block may
        # appear without a following INTRODUCTION header in PDF extraction)
        r"Keywords[\s—\-]+(.+?)(?:\n\n|\Z)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.S | re.I)
        if m:
            raw = _cleanup(m.group(1))
            kws = [k.strip() for k in re.split(r",|;", raw) if k.strip()]
            if kws:
                return kws
    return []


def _extract_sections(text: str) -> list[Section]:
    """Extract sections from a PDF's text, supporting both IEEE (Roman numeral)
    and ACM (Arabic numeral) section heading styles.

    Strategy:
    1. Try IEEE-style: ``I. INTRODUCTION``, ``II. RELATED WORK`` etc.
    2. If no matches, try ACM-style: ``1. Introduction``, ``2 Background`` etc.
    3. Fall back to a single-section "Body" if neither pattern fires.
    """
    # Strategy 1: IEEE Roman numeral sections (ALL-CAPS headings)
    ieee_pattern = re.compile(r"(?:^|\n)([IVX]+\.\s+[A-Z][A-Z\s\-]+)(?:\n|$)")
    matches = list(ieee_pattern.finditer(text))
    acm_style = False

    # Strategy 2: ACM Arabic numeral sections (Title-Case headings)
    if not matches:
        acm_pattern = re.compile(
            r"(?:^|\n)(\d+\.?\s+[A-Z][A-Za-z][A-Za-z\s\-]{1,60})(?:\n|$)"
        )
        matches = list(acm_pattern.finditer(text))
        acm_style = bool(matches)

    sections: list[Section] = []
    for i, match in enumerate(matches):
        raw_title = _cleanup(match.group(1))
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = _cleanup(text[start:end])

        # Detect trailing single-capital-letter bleed: in multi-column PDFs the
        # first character of the section body sometimes appears at the end of the
        # header line (e.g. "I. INTRODUCTION D" where "D" is the start of
        # "DIGITAL").  Strip from title and prepend to content so no text is lost.
        bleed = re.search(r"\s+([A-Z])$", raw_title)
        if bleed:
            raw_title = raw_title[: bleed.start()].strip()
            content = bleed.group(1) + content

        content = _strip_section_noise(content)
        # Strip a repeated section header at the start of the content block.
        normalized_title = _normalize_heading(raw_title, acm_style=acm_style).lower()
        first_line_end = content.find(". ")
        first_part = content[:first_line_end + 1] if first_line_end > 0 else content[:80]
        if re.sub(r"[^a-z]", "", first_part.lower()) == re.sub(r"[^a-z]", "", normalized_title):
            content = _cleanup(content[len(first_part):])
        if len(content) < 40 or not re.search(r"[a-zA-Z]{6,}", content):
            continue
        sections.append(Section(title=_normalize_heading(raw_title, acm_style=acm_style), content=content))

    if not sections:
        return _fallback_sections(text)
    return sections


def _extract_reference_placeholders(text: str) -> list[Reference]:
    """Extract citation keys from the paper body.

    Handles two styles:
    - Numeric:     ``[1]``, ``[12]`` → key ``ref1``, ``ref12``
    - Author-year: ``[Smith 2020]``, ``[Smith et al. 2023]`` → key ``smith2020`` etc.

    Numeric keys use the ``ref<N>`` convention so they can be round-tripped by
    ``_convert_bracket_citations`` in pdf_postprocess.py.
    """
    seen: list[str] = []
    refs: list[Reference] = []

    # Numeric citations: [1], [12], [1, 2], [1-3]
    for m in re.finditer(r"\[(\d+(?:[,\-]\s*\d+)*)\]", text):
        for num_str in re.split(r"[,\-]", m.group(1)):
            num_str = num_str.strip()
            if num_str.isdigit():
                key = f"ref{num_str}"
                if key not in seen:
                    seen.append(key)
                    refs.append(Reference(key=key, raw=f"placeholder for {key}"))

    # Author-year citations (ACM style): [Smith 2020], [Smith et al. 2023]
    if not refs:
        for m in re.finditer(
            r"\[([A-Z][A-Za-z]+(?:\s+et\s+al\.?)?\s+(?:19|20)\d{2}[a-z]?)\]",
            text,
        ):
            raw_cite = m.group(1).strip()
            # Derive a stable key: "smith2020" from "Smith 2020"
            words = raw_cite.split()
            last_name = re.sub(r"[^a-z]", "", words[0].lower())
            year = next((w for w in words if re.match(r"(?:19|20)\d{2}", w)), "")
            key = f"{last_name}{year}"
            if key and key not in seen:
                seen.append(key)
                refs.append(Reference(key=key, raw=raw_cite))

    return refs


def _strip_section_noise(content: str) -> str:
    content = re.sub(r"Authorized licensed use limited to:.*?Restrictions apply\.", " ", content, flags=re.I | re.S)
    content = re.sub(r"IEEE INFOCOM.*?Networks", " ", content, flags=re.I | re.S)
    content = re.sub(r"\b(?:Scan|Registration)\b\s+\d+(?:\s+\d+)*", " ", content)
    return _cleanup(content)


def _fallback_sections(text: str) -> list[Section]:
    chunks = [c.strip() for c in re.split(r"\n\n+", text) if c.strip()]
    if not chunks:
        return []
    body = " ".join(chunks[3:]) if len(chunks) > 3 else " ".join(chunks)
    return [Section(title="Body", content=_cleanup(body))]


def _normalize_heading(title: str, acm_style: bool = False) -> str:
    """Strip section-number prefix and title-case the heading words.

    Handles both IEEE Roman numeral prefixes (``I.``, ``II.``) and ACM
    Arabic numeral prefixes (``1.``, ``2``, ``1.1``).
    """
    if acm_style:
        title = re.sub(r"^\d+\.?\d*\s*", "", title).strip()
    else:
        title = re.sub(r"^[IVX]+\.\s*", "", title).strip()
    words = [w.capitalize() for w in title.split()]
    return " ".join(words)


def _cleanup(text: str) -> str:
    text = text.replace("�", "-")
    text = text.replace("\u2019", "'")
    text = text.replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()
