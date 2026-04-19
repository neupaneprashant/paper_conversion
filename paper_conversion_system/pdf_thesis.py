from __future__ import annotations

import re
from .models import CanonicalPaperRepresentation, Section, Reference


# Generic front-matter noise to avoid classifying as authors.
# No specific names are hardcoded — only role/section labels that commonly
# appear in dissertation/thesis front matter.
BLOCKED_AUTHOR_PHRASES = {
    "Dissertation Presented",
    "Thesis Presented",
    "Partial Fulfillment",
    "Doctor Of Philosophy",
    "Doctor Of Science",
    "Master Of Science",
    "Supervisor Of Dissertation Research",
    "Supervisor Of Thesis Research",
    "Date Of Dissertation Defense",
    "Date Of Thesis Defense",
    "Graduate School",
    "Committee Members",
    "Committee Chair",
    "Dean Of The Graduate School",
    "Advisor",
    "Co Advisor",
    "Chair",
}


def parse_thesis_text_to_cpr(text: str, source_path: str = "") -> CanonicalPaperRepresentation:
    """Parse a thesis/dissertation-style PDF text blob into CPR.

    This parser is intentionally separate from the paper parser because
    thesis PDFs contain title pages, committee blocks, TOCs, list-of-figures,
    acknowledgments, and chapter structures that can badly confuse a paper-first
    extraction path.

    Current design priority is graceful degradation: prefer a coarse but believable
    chapter-oriented CPR over a confident-looking paper parse built from the wrong
    document signals.

    Body extraction order matters. We strip the TOC / lists *before* picking the
    body start offset because the TOC usually contains a "CHAPTER 1 INTRODUCTION"
    entry that would otherwise be mistaken for the real chapter 1.
    """
    title = _extract_title(text)
    authors = _extract_authors(text)
    abstract = _clean_abstract(
        _extract_named_block(
            text,
            "ABSTRACT",
            stop_tokens=["DEDICATION", "TABLE OF CONTENTS", "ACKNOWLEDGMENTS", "CHAPTER 1", "CHAPTER I"],
        )
    )
    stripped = _strip_toc_and_lists(text)
    body = _extract_body(stripped)
    sections = _extract_chapters(body)
    if not sections or (len(sections) == 1 and sections[0].title.lower() == "body"):
        # Fallback: if the stripped text is too aggressive and we lost the body,
        # retry with the original text but prefer a chapter offset that is not
        # the one inside the TOC.
        body_fallback = _extract_body(text, prefer_second=True)
        sections = _extract_chapters(body_fallback) or sections
    references = _extract_references(text)

    metadata = {
        "source_path": source_path,
        "ingest_mode": "pdf_thesis",
        "document_type": "thesis_dissertation",
        "warnings": [
            "Detected thesis/dissertation-style PDF; using thesis parser path rather than paper parser.",
            "Thesis parsing is body/chapter-oriented and may not preserve native paper metadata layout.",
        ],
    }

    return CanonicalPaperRepresentation(
        title=title,
        authors=authors,
        abstract=abstract,
        keywords=[],
        sections=sections,
        references=references,
        metadata=metadata,
    )


# TOC entries look like "CHAPTER 1 INTRODUCTION .......... 1" or are immediately
# followed by another section number like "1.1 Thesis Statement". Real chapter
# bodies are followed by free prose, not a numbered subsection header.
_TOC_LOOKAHEAD_RE = re.compile(
    r"(?:\.{3,}\s*\d+|\s+\d+\.\d+\s+[A-Z])",
)


def _extract_body(text: str, prefer_second: bool = False) -> str:
    """Pick the offset at which the real body begins.

    ``prefer_second`` picks the second occurrence of the chapter-1 marker,
    which is useful when the first occurrence is still the TOC entry after
    TOC stripping was too cautious.
    """
    lower = text.lower()
    start = 0
    patterns = [
        r"chapter\s+1\s+introduction",
        r"chapter\s+i\s+introduction",
        r"chapter\s+1\b",
        r"chapter\s+i\b",
        r"\n\s*1\.\s+introduction\b",
    ]
    for pattern in patterns:
        matches = list(re.finditer(pattern, lower))
        if not matches:
            continue
        # Prefer a match whose lookahead doesn't look like a TOC dotted-leader.
        chosen = None
        for m in matches:
            tail = text[m.end(): m.end() + 60]
            if _TOC_LOOKAHEAD_RE.search(tail):
                continue
            chosen = m
            break
        if chosen is None and matches:
            # Fall back: if prefer_second requested, pick the second match
            # (if present) since the first is almost certainly the TOC entry.
            chosen = matches[1] if prefer_second and len(matches) > 1 else matches[-1]
        if chosen is not None:
            start = chosen.start()
            break
    if start == 0:
        # Last resort: look for a standalone Introduction header that isn't
        # inside the TOC.
        m = re.search(r"\bintroduction\b", lower)
        if m:
            start = m.start()
    body = text[start:]
    body = re.sub(r"APPENDI(?:X|CES)\s+[A-Z].*", "", body, flags=re.I | re.S)
    return body


def _extract_title(text: str) -> str:
    m = re.search(
        r"entitled\s+(.*?)\s+(?:be\s+accepted|is\s+approved|is\s+presented|has\s+been\s+accepted)",
        text,
        re.I | re.S,
    )
    if m:
        return _clean(m.group(1))
    # Fall back to picking the first plausible multi-word line from the first page.
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    candidates = [
        l for l in lines[:40]
        if len(l.split()) > 4 and len(l) < 200 and not l.isdigit()
    ]
    return _clean(candidates[0]) if candidates else "Untitled Thesis"


def _extract_authors(text: str) -> list[str]:
    m = re.search(
        r"(?:prepared|submitted|presented)\s+by\s+([A-Z][A-Za-z\s.,'-]+?)\s+(?:entitled|in\s+partial|for\s+the\s+degree)",
        text,
        re.I | re.S,
    )
    if m:
        raw = _clean(m.group(1))
        raw = re.sub(r",.*$", "", raw).strip()
        return [raw] if raw else []
    # Heuristic: look for "by <Name>" in the first page.
    m2 = re.search(r"\bby\s+([A-Z][A-Za-z.\- ]{4,60})", text[:4000])
    if m2:
        candidate = _clean(m2.group(1))
        if candidate and candidate not in BLOCKED_AUTHOR_PHRASES:
            return [candidate]
    return []


def _extract_named_block(text: str, name: str, stop_tokens: list[str]) -> str:
    pattern = rf"{name}\s+(.*?)(?:{'|'.join(stop_tokens)})"
    m = re.search(pattern, text, re.I | re.S)
    return _clean(m.group(1)) if m else ""


def _clean_abstract(text: str) -> str:
    text = re.sub(r"\b[ivxlcdm]+\b", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_toc_and_lists(text: str) -> str:
    """Remove TOC / list-of-figures / list-of-tables / committee / ack pages.

    Thesis PDFs serialize these sections before the real content. They're
    nearly always a sequence of numbered headings followed by dotted page
    leaders (``..........  42``). We remove them up to the first chapter
    marker that is NOT immediately followed by a TOC-style lookahead.

    We also strip common dotted-leader artefacts that sometimes survive the
    block removal when the PDF's TOC has irregular spacing.
    """
    for header in [
        "TABLE OF CONTENTS",
        "LIST OF FIGURES",
        "LIST OF TABLES",
        "LIST OF ABBREVIATIONS",
        "LIST OF SYMBOLS",
        "ACKNOWLEDGMENTS",
        "ACKNOWLEDGEMENTS",
        "DEDICATION",
    ]:
        text = re.sub(
            rf"{header}.*?(?=CHAPTER\s+(?:\d+|[IVXL]+)\b|\Z)",
            " ",
            text,
            flags=re.I | re.S,
        )
    # Dotted-leader page entries: "Introduction ............  1"
    text = re.sub(r"\.{5,}\s*\d+", " ", text)
    # TOC-style numbered entries in a run: "1.1 Foo 1.2 Bar 1.2.1 Baz" - strip when
    # they form long unbroken runs of numbered headings without prose in between.
    text = re.sub(
        r"(?:(?:\b\d+(?:\.\d+)+\s+[A-Z][A-Za-z\-\s]{3,60}?(?=(?:\s+\d+(?:\.\d+)+\s+[A-Z])|$)){3,})",
        " ",
        text,
    )
    text = re.sub(r"\b[ivxlcdm]+\b", " ", text, flags=re.I)
    return text


def _extract_chapters(text: str) -> list[Section]:
    pattern = re.compile(r"CHAPTER\s+(\d+|[IVXL]+)\s+([A-Z][A-Z\s\-/]+)")
    matches = list(pattern.finditer(text))
    sections: list[Section] = []
    seen_titles: set[str] = set()
    for i, match in enumerate(matches):
        title = _normalize_heading(match.group(2))
        if title in seen_titles or not title:
            continue
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = _clean(text[start:end])
        content = re.sub(r"\b\d+(?:\.\d+)+\s+[A-Z][A-Za-z].{0,60}", " ", content)
        content = re.sub(r"\b(?:Figure|Fig\.|Table)\s+\d+[\-\d:\.]*.*?(?=\.|\n)", " ", content, flags=re.I)
        content = re.sub(r"\s+", " ", content).strip()
        if len(content) < 150:
            continue
        sections.append(Section(title=title, content=content))
        seen_titles.add(title)
    if not sections:
        sections.append(Section(title="Body", content=_clean(text)))
    return sections


def _extract_references(text: str) -> list[Reference]:
    matches = list(re.finditer(r"(?:REFERENCES|BIBLIOGRAPHY|WORKS\s+CITED)", text, re.I))
    if not matches:
        return []
    start = matches[-1].end()
    refs_text = text[start:]
    refs_text = re.sub(r"LIST OF FIGURES.*", "", refs_text, flags=re.I | re.S)
    refs_text = re.sub(r"LIST OF TABLES.*", "", refs_text, flags=re.I | re.S)
    refs_text = re.sub(r"APPENDI(?:X|CES).*", "", refs_text, flags=re.I | re.S)
    refs: list[Reference] = []
    entries = list(re.finditer(r"\[(\d+)\]\s*(.*?)(?=\s*\[\d+\]|$)", refs_text, flags=re.S))
    for match in entries:
        idx = match.group(1)
        raw = _clean(match.group(2))
        if len(raw) < 20:
            continue
        refs.append(Reference(key=f"ref{idx}", raw=raw))
    return refs[:200]


def _normalize_heading(title: str) -> str:
    cleaned = " ".join([w.capitalize() for w in title.split()])
    cleaned = re.sub(r"\b([A-Z])$", "", cleaned).strip()
    return cleaned


def _clean(text: str) -> str:
    text = text.replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()
