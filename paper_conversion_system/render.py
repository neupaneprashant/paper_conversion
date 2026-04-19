from __future__ import annotations

from pathlib import Path
import re

from .models import CanonicalPaperRepresentation
from .templates import ACM_MAIN_TEMPLATE, IEEE_MAIN_TEMPLATE


def render_cpr_to_target(cpr: CanonicalPaperRepresentation, target_format: str, output_dir: Path) -> Path:
    """Render a normalized CPR object into target LaTeX files.

    Produces at minimum `main.tex` and `references.bib` in the output directory.
    The rendering logic is intentionally lightweight and heuristic; more faithful
    frontmatter and reference reconstruction can be layered on top later.

    For PDF-ingested CPR we escape LaTeX-special characters in the body so that
    raw ``$``, ``%``, ``&``, ``_``, ``#`` from extracted text don't put the
    compiler into math mode or trigger fatal errors. Source-LaTeX input is not
    escaped so existing math / macros remain intact.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ingest_mode = str(cpr.metadata.get("ingest_mode", "") or "")
    escape_body = ingest_mode.startswith("pdf")
    body = _render_body(cpr, target_format, escape_body=escape_body)
    authors = _render_authors(cpr, target_format)
    keywords_block = _render_keywords(cpr, target_format)
    extra_frontmatter = _render_extra_frontmatter(cpr, target_format)
    acknowledgments_block = _render_acknowledgments(cpr, target_format)
    abstract_text = _escape_latex_specials(cpr.abstract) if escape_body else cpr.abstract

    if target_format == "acm":
        tex = ACM_MAIN_TEMPLATE.format(
            title=cpr.title,
            authors=authors,
            abstract=abstract_text,
            keywords_block=keywords_block,
            body=body + acknowledgments_block,
            extra_frontmatter=extra_frontmatter,
        )
    elif target_format == "ieee":
        tex = IEEE_MAIN_TEMPLATE.format(
            title=cpr.title,
            authors=authors,
            abstract=abstract_text,
            keywords_block=keywords_block,
            body=body + acknowledgments_block,
            extra_frontmatter=extra_frontmatter,
        )
    else:
        raise ValueError(f"Unsupported target format: {target_format}")

    main = output_dir / "main.tex"
    refs = output_dir / "references.bib"
    main.write_text(tex, encoding="utf-8")
    refs.write_text(_render_bib_stub(cpr), encoding="utf-8")
    return main


def _render_body(cpr: CanonicalPaperRepresentation, target_format: str, escape_body: bool = False) -> str:
    chunks: list[str] = []
    for section in cpr.sections:
        title = _map_section_title(section.title, target_format)
        if title.lower() == "references":
            continue
        content = section.content.strip()
        if escape_body:
            content = _escape_latex_specials(content)
        chunks.append(f"\\section{{{title}}}\n{content}\n")
    if cpr.figures:
        chunks.append(_render_figures(cpr))
    if cpr.tables:
        chunks.append(_render_tables(cpr))
    return "\n".join(chunks)


# Characters that must be escaped when inserting raw PDF text into LaTeX body
# copy. We intentionally do NOT escape ``\`` because the CPR refinement step
# may legitimately insert ``\cite{}`` and ``\subsection{}`` markers, and
# escaping ``\`` would break those. Same reasoning for ``{`` / ``}``.
_LATEX_SPECIAL_ESCAPES = [
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("~", r"\~{}"),
    ("^", r"\^{}"),
]


def _escape_latex_specials(text: str) -> str:
    """Escape LaTeX-special characters in free prose pulled from PDFs.

    Skips escaping inside already-emitted LaTeX commands that the CPR
    refinement step injected. Because those commands use ``\\`` followed by
    letters and curly braces, and we don't escape ``\\`` / ``{`` / ``}``, the
    commands survive this pass unchanged.
    """
    if not text:
        return text
    # Skip escaping for characters that already appear to be escaped (e.g. "\%")
    # or are part of an existing ``\cite``/``\subsection`` macro.
    result = text
    for raw, escaped in _LATEX_SPECIAL_ESCAPES:
        # Only replace when not already preceded by a backslash.
        result = re.sub(
            rf"(?<!\\){re.escape(raw)}",
            lambda m, e=escaped: e,
            result,
        )
    return result


def _render_keywords(cpr: CanonicalPaperRepresentation, target_format: str) -> str:
    if not cpr.keywords:
        return ""
    joined = ", ".join(cpr.keywords)
    if target_format == "ieee":
        return f"\\begin{{IEEEkeywords}}\n{joined}\n\\end{{IEEEkeywords}}"
    return f"\\keywords{{{joined}}}"


def _render_authors(cpr: CanonicalPaperRepresentation, target_format: str) -> str:
    """Render the target-format author block.

    For ACM we emit a per-author block of ``\\author{}`` + optional
    ``\\affiliation{\\institution{...}}`` + optional ``\\email{}``. If there are
    fewer affiliations/emails than authors, we still emit a valid author entry
    rather than leaving a dangling ``\\affiliation{}`` attached to the wrong
    person. If there are more affiliations than authors, we fold the extras
    into the last author's block so no metadata is dropped silently.

    For IEEE we emit ``author \\and author`` separated blocks. Affiliation and
    email lines are appended after the last author (not interleaved) because
    IEEEtran's ``\\author`` macro doesn't use a structured per-author schema.
    """
    if not cpr.authors:
        return "Anonymous"

    aff = [str(a).strip() for a in (cpr.metadata.get("affiliations", []) or []) if str(a).strip()]
    emails = [str(e).strip() for e in (cpr.metadata.get("emails", []) or []) if str(e).strip()]

    if target_format == "acm":
        chunks: list[str] = []
        num_authors = len(cpr.authors)
        for i, name in enumerate(cpr.authors):
            chunks.append(f"\\author{{{name}}}")
            own_aff = aff[i] if i < len(aff) else None
            own_email = emails[i] if i < len(emails) else None
            if own_aff:
                chunks.append(f"\\affiliation{{\\institution{{{own_aff}}}}}")
            if own_email:
                chunks.append(f"\\email{{{own_email}}}")
            # Last author absorbs any trailing affiliations/emails we couldn't pair.
            if i == num_authors - 1:
                for extra in aff[num_authors:]:
                    chunks.append(f"\\affiliation{{\\institution{{{extra}}}}}")
                for extra in emails[num_authors:]:
                    chunks.append(f"\\email{{{extra}}}")
        return "\n".join(chunks)

    # IEEE path: classic \author{A \and B \\ Aff1 \and Aff2 \\ emails}
    author_line = " \\and ".join(cpr.authors)
    extras: list[str] = []
    if aff:
        extras.append(" \\and ".join(aff[: max(len(cpr.authors), len(aff))]))
    if emails:
        extras.append(" \\and ".join(emails[: max(len(cpr.authors), len(emails))]))
    if extras:
        return author_line + " \\\\ " + " \\\\ ".join(extras)
    return author_line


def _render_extra_frontmatter(cpr: CanonicalPaperRepresentation, target_format: str) -> str:
    if target_format == "acm":
        ccsxml = cpr.metadata.get("ccsxml", "")
        ccs_concepts = cpr.metadata.get("ccs_concepts", []) or []
        chunks: list[str] = []
        if ccsxml:
            chunks.append(f"\\begin{{CCSXML}}\n{ccsxml}\n\\end{{CCSXML}}")
        for concept in ccs_concepts:
            chunks.append(f"\\ccsdesc{{{concept}}}")
        return "\n".join(chunks)
    return ""


def _render_acknowledgments(cpr: CanonicalPaperRepresentation, target_format: str) -> str:
    ack = str(cpr.metadata.get("acknowledgments", "") or "").strip()
    if not ack:
        return ""
    if target_format == "acm":
        return f"\n\\begin{{acks}}\n{ack}\n\\end{{acks}}\n"
    return f"\n\\section*{{Acknowledgments}}\n{ack}\n"


def _map_section_title(title: str, target_format: str) -> str:
    canonical = " ".join(title.split())
    aliases = {
        "background": "Background",
        "conclusion": "Conclusion",
        "conclusions": "Conclusion",
    }
    return aliases.get(canonical.strip().lower(), canonical)


def _render_figures(cpr: CanonicalPaperRepresentation) -> str:
    chunks: list[str] = []
    for fig in cpr.figures[:3]:
        chunks.append(
            f"\\begin{{figure}}[tbp]\n\\centering\n% Figure asset unavailable from PDF ingest\n\\caption{{{fig.caption}}}\n\\label{{{fig.label}}}\n\\end{{figure}}"
        )
    return "\n\n".join(chunks)


def _render_tables(cpr: CanonicalPaperRepresentation) -> str:
    chunks: list[str] = []
    for table in cpr.tables[:2]:
        chunks.append(
            f"\\begin{{table}}[tbp]\n\\caption{{{table.caption}}}\n\\label{{{table.label}}}\n{table.latex}\n\\end{{table}}"
        )
    return "\n\n".join(chunks)


def _render_bib_stub(cpr: CanonicalPaperRepresentation) -> str:
    """Serialise CPR references into a BibTeX file.

    If an entry's ``raw`` field is already a complete ``@type{...}`` block, it
    is emitted verbatim so we don't lose fidelity on LaTeX-source ingests. For
    PDF-ingested references, we fall through to a structured guesser that tries
    to recover author/title/year/venue from common IEEE/ACM reference shapes.
    """
    if not cpr.references:
        return "% No references extracted\n"
    entries: list[str] = []
    seen_keys: set[str] = set()
    for ref in cpr.references:
        if ref.key in seen_keys:
            continue
        seen_keys.add(ref.key)
        raw = (ref.raw or "").strip()
        if raw.startswith("@") and raw.endswith("}") and "{" in raw:
            entries.append(raw)
            continue
        entry_type, fields = _guess_bibtex_fields(ref.key, ref.raw)
        field_text = "\n".join(
            [f"  {k}={{{_sanitise_bib_value(v)}}}," for k, v in fields.items() if v]
        )
        entries.append(f"@{entry_type}{{{ref.key},\n{field_text}\n}}")
    return "\n\n".join(entries) + "\n"


# Recognised venue markers. Order matters: check conference-style first, then
# journal-style, so a "Proc. of IEEE Conf." is classified as inproceedings rather
# than as a journal article.
_CONF_MARKERS = ("Proceedings", "Proc.", "Conference", "Conf.", "Symposium", "Workshop", "Congress")
_JOURNAL_MARKERS = ("Transactions", "Journal", "Letters", "Magazine", "Review", "ACM Comput", "Commun. ACM")


def _guess_bibtex_fields(key: str, raw: str) -> tuple[str, dict[str, str]]:
    """Heuristically split a raw reference string into BibTeX fields.

    Handles common IEEE/ACM reference shapes like::

        A. B. Author, C. Author, "Title of paper," in Proc. X Conf., 2023, pp. 1-10.
        D. Author and E. Author, Title, Journal Name, vol. 12, no. 3, pp. 4-5, 2020.

    The parser is intentionally conservative: unrecognised fragments are kept in
    a ``note`` field so nothing from the original reference is lost.
    """
    text = re.sub(r"\s+", " ", raw or "").strip().rstrip(".")
    if not text:
        return "misc", {"note": raw or ""}

    year = ""
    year_match = re.search(r"\b(19|20)\d{2}\b", text)
    if year_match:
        year = year_match.group(0)

    pages = ""
    pages_match = re.search(r"pp\.\s*([\d\u2013\u2014-]+)", text, re.I)
    if pages_match:
        pages = pages_match.group(1).replace("\u2013", "-").replace("\u2014", "-")

    volume = ""
    vol_match = re.search(r"vol\.\s*(\d+)", text, re.I)
    if vol_match:
        volume = vol_match.group(1)

    number = ""
    num_match = re.search(r"no\.\s*(\d+)", text, re.I)
    if num_match:
        number = num_match.group(1)

    # Title: try quoted first, then an "italicised" style isn't recoverable from
    # PDF ingest, so we fall back to the chunk between the first comma block and
    # the venue marker.
    title = ""
    quoted = re.search(r'[\u201c"]([^\u201d"]+)[\u201d"]', text)
    if quoted:
        title = quoted.group(1).strip().rstrip(",.")

    # Author field: everything before the first quoted title or the first
    # venue/journal marker. Stop on a quote, " in " (which usually marks the
    # venue transition), or a parenthesised year.
    author = ""
    cutoff_idx = len(text)
    cutoff_candidates: list[int] = []
    for marker in ('"', "\u201c", " in Proc", " in Proc.", ", in ", ", Proc", ", Proceedings"):
        idx = text.find(marker)
        if idx != -1:
            cutoff_candidates.append(idx)
    for marker in _CONF_MARKERS + _JOURNAL_MARKERS:
        idx = text.find(marker)
        if idx > 0:
            cutoff_candidates.append(idx)
    if cutoff_candidates:
        cutoff_idx = min(cutoff_candidates)
    author = text[:cutoff_idx].strip().rstrip(",")
    # If the "author" block is suspiciously long, it's probably run-together
    # content; clip it so BibTeX doesn't choke on a giant author list.
    if len(author) > 240:
        author = author[:240]

    # Venue: between the end of the title and the next year/volume/page hint.
    venue_start = 0
    if quoted:
        venue_start = quoted.end()
    elif cutoff_idx < len(text):
        venue_start = cutoff_idx
    venue_text = text[venue_start:].strip().lstrip(",:.")

    booktitle = ""
    journal = ""
    lowered = venue_text.lower()
    if any(m.lower() in lowered for m in _CONF_MARKERS):
        # Keep only up to the first year or page marker for a cleaner booktitle.
        stop = re.search(r"\b(?:vol\.|no\.|pp\.|\d{4})\b", venue_text)
        booktitle = (venue_text[: stop.start()] if stop else venue_text).strip().rstrip(",. ")
    elif any(m.lower() in lowered for m in _JOURNAL_MARKERS) or "IEEE" in venue_text or "ACM" in venue_text:
        stop = re.search(r"\b(?:vol\.|no\.|pp\.|\d{4})\b", venue_text)
        journal = (venue_text[: stop.start()] if stop else venue_text).strip().rstrip(",. ")

    if booktitle:
        entry_type = "inproceedings"
    elif journal:
        entry_type = "article"
    else:
        entry_type = "misc"

    if not title:
        # Last resort: use the chunk right after the author block as the title.
        remainder = text[cutoff_idx:].strip().lstrip(",:.")
        # Cut at first venue marker so the title doesn't eat the venue.
        for marker in (" in Proc", " Proc.", ", Proceedings"):
            m_idx = remainder.find(marker)
            if m_idx > 0:
                remainder = remainder[:m_idx]
                break
        title = remainder[:180].strip().rstrip(",.") or text[:180]

    fields: dict[str, str] = {
        "author": author,
        "title": title,
        "year": year,
        "journal": journal,
        "booktitle": booktitle,
        "volume": volume,
        "number": number,
        "pages": pages,
        "note": text,
    }
    return entry_type, fields


def _sanitise_bib_value(value: str) -> str:
    """Escape characters that would break a BibTeX field value."""
    return (value or "").replace("{", "(").replace("}", ")").strip()
