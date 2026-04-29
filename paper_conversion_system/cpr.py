from __future__ import annotations

from pathlib import Path
import re

from .models import CanonicalPaperRepresentation, Section, Reference


def parse_project_to_cpr(input_path: Path, source_format: str) -> CanonicalPaperRepresentation:
    main_tex = _detect_main_tex(input_path)
    text = _read_latex_with_includes(main_tex)

    title = _match_one(text, r"\\title\{(.+?)\}")
    abstract = _extract_abstract(text)
    sections = [Section(title=s[0].strip(), content=s[1].strip()) for s in _extract_sections(text)]
    refs = _extract_references(input_path)
    authors_raw = _match_one(text, r"\\author\{(.+?)\}")
    authors = [a.strip() for a in re.split(r"\\and|,", authors_raw) if a.strip()] if authors_raw else []
    keywords = _extract_keywords(text)
    preamble = _extract_preamble(text)

    metadata = {
        "source_format": source_format,
        "main_tex": str(main_tex),
        "source_root": str(main_tex.parent),
        "acknowledgments": _extract_acknowledgments(text),
        "ccs_concepts": _extract_ccs_concepts(text),
        "ccsxml": _extract_ccsxml(text),
        "custom_macros": _extract_custom_macros(preamble),
        "source_packages": _extract_source_packages(preamble),
    }

    return CanonicalPaperRepresentation(
        title=title or "Untitled",
        authors=authors,
        abstract=abstract,
        keywords=keywords,
        sections=sections,
        references=refs,
        metadata=metadata,
    )


def _detect_main_tex(input_path: Path) -> Path:
    # Check if input is a single .tex file
    if input_path.is_file() and input_path.suffix == ".tex":
        return input_path
    
    # Check if input is a directory
    if not input_path.is_dir():
        raise FileNotFoundError(
            f"Input path not found or is not a directory: {input_path}"
        )
    
    # Search for .tex files in directory root first
    mains = list(input_path.glob("*.tex"))
    for candidate in mains:
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            if "\\begin{document}" in text:
                return candidate
        except Exception:
            continue
    
    if mains:
        # Return first .tex file found
        return mains[0]
    
    # Fall back to nested project layouts, common in downloaded archives.
    tex_files = list(input_path.rglob("*.tex"))
    for candidate in tex_files:
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            if "\\begin{document}" in text:
                return candidate
        except Exception:
            continue
    if tex_files:
        return tex_files[0]
    
    raise FileNotFoundError(
        f"No .tex files found in input project: {input_path}\n"
        f"Please provide a LaTeX project folder containing source files (main.tex, article.tex, etc.)"
    )


def _match_one(text: str, pattern: str) -> str:
    m = re.search(pattern, text, re.S)
    return m.group(1).strip() if m else ""


_INCLUDE_RE = re.compile(r"\\(?:input|include)\{([^}]+)\}")


def _read_latex_with_includes(tex_path: Path, seen: set[Path] | None = None) -> str:
    """Read a LaTeX file and inline \\input/\\include children recursively."""
    resolved = tex_path.resolve()
    seen = seen or set()
    if resolved in seen:
        return ""
    seen.add(resolved)
    text = tex_path.read_text(encoding="utf-8", errors="ignore")

    def replace_include(match: re.Match[str]) -> str:
        raw_target = match.group(1).strip()
        if not raw_target:
            return ""
        candidate = (tex_path.parent / raw_target)
        if candidate.suffix.lower() != ".tex":
            candidate_with_ext = candidate.with_suffix(".tex")
            if candidate_with_ext.exists():
                candidate = candidate_with_ext
        if candidate.exists() and candidate.is_file():
            return _read_latex_with_includes(candidate, seen)
        return match.group(0)

    return _INCLUDE_RE.sub(replace_include, text)


def _extract_abstract(text: str) -> str:
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", text, re.S)
    return m.group(1).strip() if m else ""


# Patterns that terminate a section's content when scanning a LaTeX main file.
# Without this, everything after the last \section gets swallowed into that
# section, including \bibliography{...} and \end{document}, which then gets
# re-emitted inside the new template body and produces a malformed output.
_SECTION_TERMINATORS = re.compile(
    r"\\(?:bibliography|bibliographystyle|printbibliography|end\s*\{document\}|appendix)\b",
    re.I,
)


def _extract_sections(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"\\section\{(.+?)\}", text))
    results: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        title = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        term = _SECTION_TERMINATORS.search(body)
        if term:
            body = body[: term.start()]
        results.append((title, body))
    return results


def _extract_references(input_path: Path) -> list[Reference]:
    bibs = list(input_path.glob("*.bib")) if input_path.is_dir() else list(input_path.parent.glob("*.bib"))
    refs: list[Reference] = []
    for bib in bibs:
        raw = bib.read_text(encoding="utf-8", errors="ignore")
        for entry in _iter_bib_entries(raw):
            key_match = re.match(r"@\w+\{\s*([^,\s]+)\s*,", entry)
            if not key_match:
                continue
            refs.append(Reference(key=key_match.group(1).strip(), raw=entry.strip()))
    return refs


def _iter_bib_entries(raw: str):
    """Yield complete @type{...} entries by tracking brace balance."""
    i = 0
    while i < len(raw):
        start = raw.find("@", i)
        if start == -1:
            return
        open_brace = raw.find("{", start)
        if open_brace == -1:
            return
        depth = 1
        j = open_brace + 1
        while j < len(raw) and depth > 0:
            if raw[j] == "{":
                depth += 1
            elif raw[j] == "}":
                depth -= 1
            j += 1
        if depth == 0:
            yield raw[start:j]
            i = j
        else:
            return


def _extract_keywords(text: str) -> list[str]:
    for pattern in [
        r"\\keywords\{(.+?)\}",
        r"\\begin\{IEEEkeywords\}(.*?)\\end\{IEEEkeywords\}",
    ]:
        m = re.search(pattern, text, re.S)
        if m:
            raw = m.group(1).replace("\n", " ")
            return [k.strip() for k in re.split(r",|;", raw) if k.strip()]
    return []


def _extract_acknowledgments(text: str) -> str:
    for pattern in [
        r"\\begin\{acks\}(.*?)\\end\{acks\}",
        r"\\section\*\{[Aa]cknowledg(?:e)?ments\}(.*?)(?=\\section|\\bibliography|\\end\{document\})",
    ]:
        m = re.search(pattern, text, re.S)
        if m:
            return m.group(1).strip()
    return ""


def _extract_ccs_concepts(text: str) -> list[str]:
    return [m.strip() for m in re.findall(r"\\ccsdesc(?:\[[^\]]+\])?\{([^}]+)\}", text) if m.strip()]


def _extract_ccsxml(text: str) -> str:
    m = re.search(r"\\begin\{CCSXML\}(.*?)\\end\{CCSXML\}", text, re.S)
    return m.group(1).strip() if m else ""


def _extract_preamble(text: str) -> str:
    """Return everything before \\begin{document}."""
    idx = text.find("\\begin{document}")
    return text[:idx] if idx != -1 else ""


def _extract_custom_macros(preamble: str) -> list[str]:
    """Extract custom macro definitions from the source preamble.

    Captures single-line \\newcommand, \\renewcommand, \\providecommand,
    and \\def declarations so they survive the CPR round-trip and can be
    re-emitted in the target preamble.  Multi-line macro bodies are not
    captured here to avoid fragile brace-counting.
    """
    macros: list[str] = []
    for line in preamble.splitlines():
        stripped = line.strip()
        if stripped.startswith((
            "\\newcommand",
            "\\renewcommand",
            "\\providecommand",
            "\\def\\",
        )):
            # Skip macros that redefine core formatting primitives — those
            # clash with the target template's own definitions.
            skip_prefixes = (
                "\\newcommand{\\section",
                "\\newcommand{\\subsection",
                "\\renewcommand{\\section",
                "\\renewcommand{\\subsection",
                "\\renewcommand{\\abstract",
            )
            if any(stripped.startswith(p) for p in skip_prefixes):
                continue
            macros.append(stripped)
    return macros


_USEPACKAGE_RE = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}")


def _extract_source_packages(preamble: str) -> list[str]:
    """Return package names declared in the source preamble.

    Used by the renderer to selectively forward compatible packages (e.g.
    algorithm, listings, xcolor) that are not part of the base template.
    """
    _SKIP_PACKAGES = {
        # Template-level packages already present in both base templates.
        "graphicx", "amsmath", "amssymb", "float", "placeins",
        # Class-specific; clash with the target class.
        "IEEEtran", "acmart",
    }
    packages: list[str] = []
    for m in _USEPACKAGE_RE.finditer(preamble):
        for pkg in m.group(1).split(","):
            name = pkg.strip()
            if name and name not in _SKIP_PACKAGES and name not in packages:
                packages.append(name)
    return packages
