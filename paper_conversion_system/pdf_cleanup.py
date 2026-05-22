from __future__ import annotations

import re
from collections import Counter


HEADER_FOOTER_PATTERNS = [
    r"Authorized licensed use limited to:.*?Restrictions apply\.",
    r"IEEE INFOCOM.*?Networks",
    r"IEEE Conference on Computer Communications Workshops.*",
    r"978-[^\n]+©\s*\d{4} IEEE",
    r"DOI:\s*10\.[^\s]+",
    r"Downloaded on .*? UTC from IEEE Xplore\.",
    r"ACM Reference Format:.*",
    r"Permission to make digital or hard copies.*",
]

INLINE_NOISE_PATTERNS = [
    r"0\s+5\s+10\s+15\s+20\s+25\s+30\s+35",
    r"Scan\s+\d+",
    r"Registration\s+\d+(?:\s+\d+)+",
]

# The aggressive pass strips bare figure/table heading tokens. The table
# pattern only removes a "TABLE <n>" token that is NOT followed by more
# text on the same span, so it never eats an in-sentence cross-reference
# ("TABLE I shows the estimated positions") nor an inline caption title
# ("TABLE I FIRST RSSI VALUE RESULTS" — caption lines are handled wholesale
# by _strip_standalone_visual_captions instead). ``\b`` forces the numeral
# to match as a whole token so the regex cannot backtrack ("II" -> "I") to
# satisfy the lookahead and leave a stray "I" behind in the prose.
AGGRESSIVE_PATTERNS = [
    r"Fig\.\s*\d+\.\s*[^\n]+",
    r"TABLE\s+[IVXLC0-9]+\b(?!\s+[A-Za-z])",
]

_FRONTMATTER_BOUNDARY_RE = re.compile(r"\b(?:abstract|index\s*terms|keywords|introduction)\b", re.I)


def clean_pdf_text(raw_text: str, mode: str = "safe") -> tuple[str, dict]:
    """Clean raw PDF text before structure extraction.

    `safe` mode removes obvious repeated boilerplate while trying hard not to
    destroy section/abstract cues. `aggressive` mode layers stronger stripping on
    top and should only be used when later confidence checks say the document is
    already well-understood.
    """
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]

    repeated = _find_repeated_lines(lines)
    cleaned_lines: list[str] = []
    removed_lines: list[str] = []
    preserved_repeated_frontmatter: set[str] = set()
    frontmatter_open = True

    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        if _FRONTMATTER_BOUNDARY_RE.search(stripped):
            frontmatter_open = False
        if stripped in repeated and len(stripped) > 30:
            if frontmatter_open and stripped not in preserved_repeated_frontmatter:
                preserved_repeated_frontmatter.add(stripped)
                cleaned_lines.append(stripped)
                continue
            removed_lines.append(stripped)
            continue
        if _matches_any(stripped, HEADER_FOOTER_PATTERNS):
            removed_lines.append(stripped)
            continue
        if _looks_like_header_footer(stripped):
            removed_lines.append(stripped)
            continue
        cleaned_lines.append(stripped)

    text = "\n".join(cleaned_lines)

    for pattern in HEADER_FOOTER_PATTERNS + INLINE_NOISE_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.I | re.S)

    text = _normalize_section_boundaries(text)
    text = _drop_reference_tail_noise(text)
    text = _normalize_spacing(text)
    text = _repair_hyphenation(text)
    text = _normalize_symbols(text)

    if mode == "aggressive":
        text = aggressive_cleanup_pass(text)

    return text, {
        "mode": mode,
        "removed_repeated_lines": sorted(repeated)[:50],
        "removed_line_count": len(removed_lines),
    }


def aggressive_cleanup_pass(text: str) -> str:
    for pattern in AGGRESSIVE_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.I | re.S)
    text = _normalize_spacing(text)
    return text


def _find_repeated_lines(lines: list[str]) -> set[str]:
    counts = Counter(line.strip() for line in lines if line.strip())
    return {line for line, count in counts.items() if count >= 2 and len(line) > 25}


def _matches_any(line: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, line, re.I) for pattern in patterns)


def _drop_reference_tail_noise(text: str) -> str:
    text = re.sub(r"References\s+IEEE INFOCOM.*", "References", text, flags=re.I | re.S)
    text = re.sub(r"References\s+Authorized licensed use limited to:.*", "References", text, flags=re.I | re.S)
    return text


def _looks_like_header_footer(line: str) -> bool:
    if len(line) < 10:
        return False
    if re.search(r"Downloaded on .* UTC", line, re.I):
        return True
    if re.search(r"Authorized licensed use limited to", line, re.I):
        return True
    if re.search(r"^\d+\s*/\s*\d+$", line):
        return True
    if re.search(r"^\d{4} IEEE$", line):
        return True
    return False


def _normalize_section_boundaries(text: str) -> str:
    # Generic: any roman-numeral heading followed by all-caps title starts a new line.
    return re.sub(
        r"\s+((?:I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\.\s+[A-Z][A-Z\s\-]+)",
        r"\n\1",
        text,
    )


def _normalize_spacing(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _repair_hyphenation(text: str) -> str:
    text = re.sub(r"(\w)-\s+([a-z])", r"\1\2", text)
    return text


def _normalize_symbols(text: str) -> str:
    text = text.replace("�", "-")
    text = text.replace("•", "")
    text = text.replace("‘", "'").replace("’", "'")
    text = text.replace("“", '"').replace("”", '"')
    return text
