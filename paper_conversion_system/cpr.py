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

    source_bib = _load_source_bibliography(input_path)

    metadata = {
        "source_format": source_format,
        "main_tex": str(main_tex),
        "source_root": str(main_tex.parent),
        "source_latex_expanded": text,
        "source_preamble": preamble,
        "source_has_real_bib": bool(source_bib),
        "source_bib_text": source_bib,
        "acknowledgments": _extract_acknowledgments(text),
        "ccs_concepts": _extract_ccs_concepts(text),
        "ccsxml": _extract_ccsxml(text),
        "custom_macros": _extract_custom_macros(preamble),
        "source_packages": _extract_source_packages(preamble),
        "tikz_libraries": _extract_tikz_libraries(preamble),
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


def _load_source_bibliography(input_path: Path) -> str:
    """Return the richest .bib content from source, if available.

    Preference order:
    1. references.bib
    2. largest .bib file in project root
    """
    bibs = list(input_path.glob("*.bib")) if input_path.is_dir() else list(input_path.parent.glob("*.bib"))
    if not bibs:
        return ""
    preferred = None
    for bib in bibs:
        if bib.name.lower() == "references.bib":
            preferred = bib
            break
    if preferred is None:
        preferred = max(bibs, key=lambda p: p.stat().st_size if p.exists() else 0)
    try:
        return preferred.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


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


# Macro definitions that redefine core formatting primitives clash with the
# target template — skip them.
_MACRO_SKIP_PREFIXES = (
    "\\newcommand{\\section",
    "\\newcommand{\\subsection",
    "\\newcommand{\\subsubsection",
    "\\renewcommand{\\section",
    "\\renewcommand{\\subsection",
    "\\renewcommand{\\abstract",
    "\\renewcommand{\\thefootnote",
    "\\renewcommand{\\footnotetextcopyrightpermission",
    "\\renewcommand{\\familydefault",
    "\\renewcommand{\\baselinestretch",
    "\\renewcommand{\\maketitle",
    "\\renewcommand{\\title",
    "\\renewcommand{\\author",
    "\\renewcommand{\\bibname",
    "\\setcounter{secnumdepth",
    "\\setcounter{tocdepth",
    "\\setcounter{page",
    "\\setlength{\\textwidth",
    "\\setlength{\\textheight",
    "\\setlength{\\columnwidth",
    "\\setlength{\\columnsep",
    "\\setlength{\\oddsidemargin",
    "\\setlength{\\evensidemargin",
    "\\setlength{\\topmargin",
    "\\setlength{\\headheight",
    "\\setlength{\\headsep",
    "\\setlength{\\parindent",
    "\\setlength{\\parskip",
    "\\setlength{\\footskip",
    "\\setlength{\\marginparwidth",
    "\\setlength{\\marginparsep",
)

# Macro definition triggers we scan for
_MACRO_TRIGGERS = (
    "\\newcommand",
    "\\renewcommand",
    "\\providecommand",
    "\\DeclareRobustCommand",
    "\\DeclarePairedDelimiter",
    "\\newenvironment",
    "\\renewenvironment",
    "\\newtheorem",
    "\\newcounter",
    "\\setcounter",
    "\\newlength",
    "\\setlength",
    "\\def\\",
    "\\let\\",
)


def _brace_scan(text: str, start: int) -> int:
    """Return the index just past the closing '}' that balances text[start] == '{'.

    Returns ``start`` unchanged if text[start] is not '{' or no balance found.
    """
    if start >= len(text) or text[start] != "{":
        return start
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            i += 2  # skip escaped character
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return start  # unbalanced — return unchanged


def _extract_custom_macros(preamble: str) -> list[str]:
    """Extract macro definitions from the source preamble using brace-balanced parsing.

    Handles multi-line bodies — common for TeX logo macros such as XeLaTeX or
    LuaLaTeX — by scanning forward with brace counting rather than splitting on
    newlines.  Each extracted definition is normalised to a single line so it can
    be safely re-emitted in the target preamble without white-space artefacts.
    """
    macros: list[str] = []
    i = 0
    n = len(preamble)

    while i < n:
        # Locate the next macro-trigger starting from position i.
        best_pos = n
        best_trigger = ""
        for trigger in _MACRO_TRIGGERS:
            pos = preamble.find(trigger, i)
            if pos != -1 and pos < best_pos:
                best_pos = pos
                best_trigger = trigger

        if best_pos == n:
            break  # no more triggers

        # Skip triggers that appear inside a comment (% before trigger on same line).
        line_start = preamble.rfind("\n", 0, best_pos) + 1
        pre_trigger = preamble[line_start:best_pos]
        if "%" in pre_trigger:
            i = best_pos + 1
            continue

        # Walk forward from the trigger to collect the complete definition.
        # Grammar covered (* = optional star suffix, [...] = optional bracket arg):
        #   \newcommand[*]            {cmd} [n] [default] {body}
        #   \renewcommand[*]          {cmd} [n] [default] {body}
        #   \providecommand[*]        {cmd} [n] [default] {body}
        #   \DeclareRobustCommand[*]  {cmd} [n] [default] {body}
        #   \def\cmd                  {body}
        #   \let\cmd \target               (no braces; bare command names)
        #   \newenvironment           {name} [n] [default] {begin} {end}
        #   \renewenvironment         {name} [n] [default] {begin} {end}
        #   \newtheorem               {env} [counter] {display} [reset-by]
        #   \newcounter               {name} [reset-by]
        #   \setcounter               {name} {value}
        #   \newlength                {\dim}
        #   \setlength                {\dim} {value}
        j = best_pos + len(best_trigger)
        # Skip optional '*' after macro-defining commands
        if j < n and preamble[j] == "*":
            j += 1

        # Special path for \let\cmd\target (or \let\cmd=\target).
        if best_trigger == "\\let\\":
            # j currently points at the first letter of the source-cmd name.
            head = re.match(r"[A-Za-z@]+\*?\s*=?\s*\\[A-Za-z@]+\*?", preamble[j:])
            if head:
                end = j + head.end()
            else:
                end = j  # malformed; keep nothing
            macro_text = " ".join(preamble[best_pos:end].split()) if end > best_pos else ""
            if macro_text and len(macro_text) <= 200 and macro_text not in macros:
                macros.append(macro_text)
            i = end if end > best_pos else best_pos + 1
            continue

        # Standard path: collect brace and bracket groups (up to 4).
        groups_collected = 0
        end = j
        while j < n and groups_collected < 5:
            # Skip inter-group whitespace including newlines for multi-line defs.
            while j < n and preamble[j] in " \t\n\r":
                j += 1
            if j >= n:
                break
            ch = preamble[j]
            if ch == "{":
                new_j = _brace_scan(preamble, j)
                if new_j == j:
                    break  # unbalanced
                end = new_j
                groups_collected += 1
                j = new_j
            elif ch == "[":
                close = preamble.find("]", j + 1)
                if close == -1:
                    break
                end = close + 1
                groups_collected += 1
                j = end
            elif ch == "\\" and best_trigger == "\\def\\":
                # \def\cmd{body}: cmd is a \word, not braced
                m = re.match(r"\\[A-Za-z@]+\*?", preamble[j:])
                if m:
                    j += m.end()
                    end = j
                else:
                    break
            else:
                break

        # Strip LaTeX line comments (%…\n) BEFORE collapsing whitespace.
        # Multi-line macro bodies use '%' as a line-continuation token; if we
        # collapse to a single line first, every '%' becomes a comment that
        # kills the rest of the definition (e.g. "\kern…" is silently dropped).
        raw_slice = re.sub(r"%[^\n]*", "", preamble[best_pos:end])
        macro_text = " ".join(raw_slice.split())
        if (
            macro_text
            and not any(macro_text.startswith(p) for p in _MACRO_SKIP_PREFIXES)
            and len(macro_text) <= 600
            and macro_text not in macros
        ):
            macros.append(macro_text)

        i = end if end > best_pos else best_pos + 1

    return macros


_USEPACKAGE_RE = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}")
_TIKZLIB_RE = re.compile(r"\\usetikzlibrary\{[^}]+\}")
_PGFLIB_RE = re.compile(r"\\usepgfplotslibrary\{[^}]+\}")


def _extract_tikz_libraries(preamble: str) -> list[str]:
    """Return TikZ/pgfplots library declarations found in the preamble.

    These are forwarded verbatim to the output preamble whenever the source
    paper uses TikZ — without them, complex flowcharts (Figure 4 in the
    sample paper, for instance) render with overlapping or missing nodes.
    """
    libs: list[str] = []
    for m in _TIKZLIB_RE.finditer(preamble):
        if m.group(0) not in libs:
            libs.append(m.group(0))
    for m in _PGFLIB_RE.finditer(preamble):
        if m.group(0) not in libs:
            libs.append(m.group(0))
    return libs


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
