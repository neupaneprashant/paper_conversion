from __future__ import annotations

from pathlib import Path
import re
import fitz

from .models import CanonicalPaperRepresentation, Section, Reference
from .pdf_cleanup import clean_pdf_text, aggressive_cleanup_pass
from .pdf_doctype import detect_pdf_document_type, extract_thesis_body_text
from .pdf_postprocess import refine_cpr_from_pdf
from .pdf_thesis import parse_thesis_text_to_cpr


def parse_pdf_to_cpr(pdf_path: Path, source_format_hint: str | None = None, cleanup_mode: str = "safe") -> CanonicalPaperRepresentation:
    """Parse a PDF into CPR.

    This function is the main PDF ingestion entrypoint. It detects document
    type, routes thesis/dissertation PDFs to the thesis-specific parser,
    otherwise applies PDF cleanup, extracts CPR fields, refines the CPR,
    and optionally attempts an aggressive cleanup pass when confidence is high.
    """
    doc = fitz.open(str(pdf_path))
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

    cpr = CanonicalPaperRepresentation(
        title=title or pdf_path.stem,
        authors=authors,
        abstract=abstract,
        keywords=keywords,
        sections=sections,
        references=references,
        metadata=metadata,
    )
    cpr = refine_cpr_from_pdf(cpr)

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
        else:
            cpr.metadata["cleanup"]["aggressive_applied"] = False
    else:
        cpr.metadata["cleanup"]["aggressive_applied"] = False

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
    authors: list[str] = []
    for line in lines[idx:idx + 30]:
        if _FRONTMATTER_END_RE.search(line):
            break
        if "@" in line:
            for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", line):
                metadata["emails"].append(email)
            continue
        if any(token in line.lower() for token in [
            "university", "department", "engineering", "science", "institute",
            "college", "school", "laboratory", "research center", "lab,", "lab ",
        ]):
            metadata["affiliations"].append(line)
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


def _looks_like_authors_line(line: str) -> bool:
    """Return True if a line looks like a list of author names."""
    s = line.strip()
    if not s or len(s) > 200:
        return False
    # A single short capitalised name line.
    if len(s.split()) <= 5 and re.match(r"^[A-Z][A-Za-z .'\-]+$", s):
        return True
    # A comma-separated list of capitalised names.
    parts = [p.strip() for p in re.split(r",|\band\b", s) if p.strip()]
    if len(parts) >= 2 and all(re.match(r"^[A-Z][A-Za-z .'\-]{1,40}$", p) for p in parts):
        return True
    return False


def _extract_abstract(text: str) -> str:
    patterns = [
        r"Abstract[\s—-]+(.*?)(?:Index Terms|Keywords|I\.\s*INTRODUCTION)",
        r"Abstract\s*(.*?)(?:Index Terms|Keywords|I\.\s*INTRODUCTION)",
        r"Abstract\s*(.*?)(?:I\.\s*INTRODUCTION)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.S | re.I)
        if m:
            return _cleanup(m.group(1))
    return ""


def _extract_keywords(text: str) -> list[str]:
    patterns = [
        r"Index Terms[\s—-]+(.*?)(?:I\.\s*INTRODUCTION)",
        r"Keywords[\s—-]+(.*?)(?:I\.\s*INTRODUCTION)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.S | re.I)
        if m:
            raw = _cleanup(m.group(1))
            return [k.strip() for k in re.split(r",|;", raw) if k.strip()]
    return []


def _extract_sections(text: str) -> list[Section]:
    pattern = re.compile(r"(?:^|\n)([IVX]+\.\s+[A-Z][A-Z\s\-]+)(?:\n|$)")
    matches = list(pattern.finditer(text))
    sections: list[Section] = []
    for i, match in enumerate(matches):
        title = _cleanup(match.group(1))
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = _cleanup(text[start:end])
        content = _strip_section_noise(content)
        if len(content) < 40:
            continue
        sections.append(Section(title=_normalize_heading(title), content=content))

    if not sections:
        return _fallback_sections(text)
    return sections


def _extract_reference_placeholders(text: str) -> list[Reference]:
    seen = []
    for m in re.finditer(r"\[(\d+)\]", text):
        key = f"ref{m.group(1)}"
        if key not in seen:
            seen.append(key)
    return [Reference(key=k, raw=f"placeholder for {k}") for k in seen]


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


def _normalize_heading(title: str) -> str:
    title = re.sub(r"^[IVX]+\.\s*", "", title).strip()
    words = [w.capitalize() if w.isupper() else w.capitalize() for w in title.split()]
    return " ".join(words)


def _cleanup(text: str) -> str:
    text = text.replace("�", "-")
    text = text.replace("\u2019", "'")
    text = text.replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()
