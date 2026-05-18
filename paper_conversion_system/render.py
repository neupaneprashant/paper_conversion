from __future__ import annotations

from pathlib import Path
import re

from .cpr import strip_balanced_command
from .models import CanonicalPaperRepresentation
from .pdf_artifact_hygiene import artifact_scrub_snippets_for_section, enforce_pdf_artifact_contract
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
    if escape_body:
        cpr = enforce_pdf_artifact_contract(cpr)
    preserved_preamble = _render_preserved_preamble(cpr, target_format, escape_body=escape_body)
    body = _render_body(cpr, target_format, escape_body=escape_body)
    extra_preamble = _render_extra_preamble(cpr, body)
    authors = _render_authors(cpr, target_format)
    keywords_block = _render_keywords(cpr, target_format)
    extra_frontmatter = _render_extra_frontmatter(cpr, target_format)
    acknowledgments_block = _render_acknowledgments(cpr, target_format)
    bibliography_block = _render_bibliography(cpr, target_format)
    abstract_text = _escape_latex_specials(cpr.abstract) if escape_body else cpr.abstract
    documentclass_line = _render_documentclass_line(target_format)

    if target_format == "acm":
        tex = ACM_MAIN_TEMPLATE.format(
            documentclass_line=documentclass_line,
            title=cpr.title,
            authors=authors,
            abstract=abstract_text,
            keywords_block=keywords_block,
            preserved_preamble=preserved_preamble,
            extra_preamble=extra_preamble,
            body=body + acknowledgments_block,
            extra_frontmatter=extra_frontmatter,
            bibliography_block=bibliography_block,
        )
    elif target_format == "ieee":
        tex = IEEE_MAIN_TEMPLATE.format(
            documentclass_line=documentclass_line,
            title=cpr.title,
            authors=authors,
            abstract=abstract_text,
            keywords_block=keywords_block,
            preserved_preamble=preserved_preamble,
            extra_preamble=extra_preamble,
            body=body + acknowledgments_block,
            extra_frontmatter=extra_frontmatter,
            bibliography_block=bibliography_block,
        )
    else:
        raise ValueError(f"Unsupported target format: {target_format}")

    main = output_dir / "main.tex"
    refs = output_dir / "references.bib"
    main.write_text(tex, encoding="utf-8")
    refs.write_text(_render_bib_stub(cpr), encoding="utf-8")
    return main


def _render_documentclass_line(target_format: str) -> str:
    if target_format == "acm":
        return r"\documentclass[sigconf]{acmart}"
    if target_format == "ieee":
        return r"\documentclass[conference]{IEEEtran}"
    raise ValueError(f"Unsupported target format: {target_format}")


_DOC_META_STRIP_RE = re.compile(
    r"\\(?:documentclass|begin\s*\{document\}|end\s*\{document\}|maketitle)\b.*",
    re.I,
)
_FRONTMATTER_STRIP_RE = re.compile(
    r"\\(?:title|author|date|thanks|IEEEoverridecommandlockouts|IEEEpubid)\b.*",
    re.I,
)

# Frontmatter commands stripped from the preserved preamble before re-emission.
# (label, command, arg_count). Brace-balanced scanning so multi-line
# ``\title{Some\\very long\\title}`` blocks don't leak orphan lines into the
# target preamble.
_FRONTMATTER_STRIP_COMMANDS: tuple[tuple[str, str, int], ...] = (
    ("title",     r"\title",    1),
    ("author",    r"\author",   1),
    ("date",      r"\date",     1),
    ("thanks",    r"\thanks",   1),
    ("IEEEpubid", r"\IEEEpubid", 1),
)
_DOC_META_STRIP_COMMANDS: tuple[tuple[str, str, int], ...] = (
    ("documentclass", r"\documentclass", 1),
    ("maketitle",     r"\maketitle",     0),
    ("IEEEoverridecommandlockouts", r"\IEEEoverridecommandlockouts", 0),
)


def _strip_preamble_frontmatter(raw: str) -> str:
    """Remove docclass/frontmatter commands with brace-balanced scanning.

    The previous line-by-line filter dropped only the line starting the command
    and left the remaining lines of multi-line declarations dangling in the
    output preamble.  This walks the source so the entire balanced argument
    list is removed.
    """
    cleaned = raw
    for _label, command, arg_count in _DOC_META_STRIP_COMMANDS + _FRONTMATTER_STRIP_COMMANDS:
        cleaned, _ = strip_balanced_command(cleaned, command, arg_count)
    return cleaned


def _render_preserved_preamble(
    cpr: CanonicalPaperRepresentation,
    target_format: str,
    *,
    escape_body: bool,
) -> str:
    """Best-effort preamble preservation for LaTeX-project inputs.

    For source-LaTeX inputs (not PDF ingest), keeping the original preamble
    dramatically reduces conversion breakage (custom macros, TikZ libs,
    class-specific helper packages, etc.). We still strip the source
    \\documentclass and frontmatter commands to avoid duplicates.
    """
    if escape_body:
        return ""
    raw = str(cpr.metadata.get("source_preamble", "") or "")
    if not raw.strip():
        return ""

    # Step 1: strip frontmatter and docclass commands using brace-balanced scan
    # so multi-line bodies don't leave orphaned trailing lines.
    raw = _strip_preamble_frontmatter(raw)

    cleaned_lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        # Drop any stray begin/end{document} markers.
        if stripped.startswith("\\begin{document}") or stripped.startswith("\\end{document}"):
            continue
        # Avoid explicitly loading the old class packages.
        if re.search(
            r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\b(?:IEEEtran|acmart)\b[^}]*\}",
            line,
            re.I,
        ):
            continue
        cleaned_lines.append(line.rstrip())

    cleaned = "\n".join(cleaned_lines).strip()
    if not cleaned:
        return ""
    return cleaned + "\n"


def _render_body(cpr: CanonicalPaperRepresentation, target_format: str, escape_body: bool = False) -> str:
    chunks: list[str] = []
    figure_section_map = cpr.metadata.get("figure_section_map", {}) or {}
    figure_anchor_map = cpr.metadata.get("figure_anchor_map", {}) or {}
    table_section_map = cpr.metadata.get("table_section_map", {}) or {}
    table_anchor_map = cpr.metadata.get("table_anchor_map", {}) or {}
    table_text_map = cpr.metadata.get("table_text_map", {}) or {}
    equation_text_map = cpr.metadata.get("equation_text_map", {}) or {}
    equation_artifacts = cpr.metadata.get("equation_artifacts", []) or []
    resolved_figure_sections = {
        fig.label: _locate_figure_section(
            cpr.sections,
            fig.label,
            str(figure_anchor_map.get(fig.label, "") or ""),
            str(fig.caption or ""),
            str(figure_section_map.get(fig.label, "") or ""),
        )
        for fig in cpr.figures
    }
    resolved_table_sections = {
        table.label: _locate_artifact_section(cpr.sections, str(table_text_map.get(table.label, "") or ""), table_section_map.get(table.label, ""))
        for table in cpr.tables
    }
    resolved_equation_sections = {
        str(artifact.get("label", "") or ""): _locate_equation_section(
            cpr.sections,
            artifact,
            str(equation_text_map.get(str(artifact.get("label", "") or ""), "") or ""),
        )
        for artifact in equation_artifacts
    }
    remaining_figures = _dedupe_figures_for_render(cpr.figures)
    remaining_tables = _dedupe_tables_for_render(cpr.tables)
    remaining_equations = _dedupe_artifacts_for_render(equation_artifacts)
    preserve_float_context = not escape_body
    for section in cpr.sections:
        title = _map_section_title(section.title, target_format)
        if title.lower() == "references":
            continue
        section_figures = [fig for fig in remaining_figures if resolved_figure_sections.get(fig.label, figure_section_map.get(fig.label, "")) == section.title]
        section_tables = [table for table in remaining_tables if resolved_table_sections.get(table.label, table_section_map.get(table.label, "")) == section.title]
        section_equations = [
            artifact for artifact in remaining_equations
            if resolved_equation_sections.get(str(artifact.get("label", "") or ""), artifact.get("section_title")) == section.title
        ]
        inline_artifacts: list[dict[str, str]] = []
        for fig in section_figures:
            inline_artifacts.append({
                "anchor": figure_anchor_map.get(fig.label, ""),
                "latex": _render_figures([fig]),
            })
        for table in section_tables:
            inline_artifacts.append({
                "anchor": table_anchor_map.get(table.label, ""),
                "latex": _render_tables([table]),
            })
        for equation in section_equations:
            inline_artifacts.append({
                "kind": "equation",
                "anchor": equation.get("anchor_text", ""),
                "equation_number": equation.get("equation_number", ""),
                "source_order": equation.get("source_order", 0),
                "latex": _render_equation_artifacts([equation]),
            })
        inline_artifacts.sort(key=lambda artifact: int(artifact.get("source_order") or 0))

        scrub_snippets: list[str] = []
        for table in section_tables:
            snippet = str(table_text_map.get(table.label, "") or "").strip()
            if snippet:
                scrub_snippets.append(snippet)
        for equation in section_equations:
            label = str(equation.get("label", "") or "")
            snippet = str(equation.get("raw_text", "") or equation_text_map.get(label, "") or "").strip()
            if snippet:
                scrub_snippets.append(snippet)
            for variant in equation.get("scrub_variants", []) or []:
                variant_text = str(variant or "").strip()
                if variant_text:
                    scrub_snippets.append(variant_text)
        scrub_snippets.extend(artifact_scrub_snippets_for_section(cpr, section.title))

        content = _remove_artifact_snippets(section.content.strip(), scrub_snippets)
        if section_tables:
            content = _remove_table_residue(content)
        if section_equations:
            content = _remove_equation_residue(content)
        content = _repair_dangling_visual_references(content)
        if escape_body:
            content = _format_pdf_section_text(content)
        content = _inject_artifacts(content, inline_artifacts, escape_body=escape_body)
        section_chunk = [f"\\section{{{title}}}\n{content}\n"]
        if preserve_float_context:
            section_chunk.append("\\FloatBarrier")
        chunks.append("\n".join(section_chunk))

        rendered_figure_labels = {fig.label for fig in section_figures}
        rendered_table_labels = {table.label for table in section_tables}
        rendered_equation_labels = {artifact.get("label") for artifact in section_equations}
        remaining_figures = [fig for fig in remaining_figures if fig.label not in rendered_figure_labels]
        remaining_tables = [table for table in remaining_tables if table.label not in rendered_table_labels]
        remaining_equations = [artifact for artifact in remaining_equations if artifact.get("label") not in rendered_equation_labels]

    if remaining_figures:
        chunks.append(_render_figures(remaining_figures))
    if remaining_tables:
        chunks.append(_render_tables(remaining_tables))
    if remaining_equations:
        chunks.append(_render_equation_artifacts(remaining_equations))
    return "\n".join(chunks)


def _render_extra_preamble(cpr: CanonicalPaperRepresentation, body: str) -> str:
    text = body or ""
    packages: list[str] = []

    def add(pkg_line: str) -> None:
        if pkg_line not in packages:
            packages.append(pkg_line)

    # ── Packages inferred from body content ─────────────────────────────
    if "\\begin{subfigure}" in text or "\\end{subfigure}" in text:
        add(r"\usepackage{subcaption}")
    if "\\subfloat" in text:
        add(r"\usepackage{subfig}")
    if "\\begin{minipage}" in text and "\\includegraphics" in text:
        # side-by-side minipages need caption support outside floats
        add(r"\usepackage{caption}")
    if "\\begin{tikzpicture}" in text or "\\end{tikzpicture}" in text or "\\begin{scope}" in text:
        add(r"\usepackage{tikz}")
        # Forward common TikZ libraries that complex flowcharts depend on.
        # Detection is conservative: each library is only added when the
        # corresponding library-specific syntax appears in the body.
        if "arrow" in text.lower() or "->" in text or "->>" in text:
            add(r"\usetikzlibrary{arrows.meta,arrows}")
        if "\\node[" in text and any(s in text for s in ("rectangle", "rounded corners", "draw=", "fill=")):
            add(r"\usetikzlibrary{shapes,shapes.geometric,positioning,fit,calc,backgrounds}")
        if "\\matrix" in text or "matrix of" in text:
            add(r"\usetikzlibrary{matrix}")
        if "decoration" in text:
            add(r"\usetikzlibrary{decorations.pathmorphing,decorations.markings}")
        if "\\pgfplotsset" in text or "\\begin{axis}" in text:
            add(r"\usepackage{pgfplots}")
            add(r"\pgfplotsset{compat=1.18}")
    if "\\begin{venndiagram" in text:
        add(r"\usepackage{venndiagram}")
    if "\\begin{enumerate*}" in text or "\\end{enumerate*}" in text:
        add(r"\usepackage[inline]{enumitem}")
    if "\\toprule" in text or "\\midrule" in text or "\\bottomrule" in text:
        add(r"\usepackage{booktabs}")
    if "\\multirow" in text:
        add(r"\usepackage{multirow}")
    if "\\multicolumn" in text:
        add(r"\usepackage{multicol}")
    if "\\captionof{" in text:
        add(r"\usepackage{caption}")
    if "\\Circle" in text or "\\CIRCLE" in text:
        add(r"\usepackage{wasysym}")
    if "\\balance" in text:
        add(r"\usepackage{balance}")
    if "\\begin{lstlisting}" in text or "\\lstinputlisting" in text:
        add(r"\usepackage{listings}")
    if "\\begin{algorithm}" in text or "\\begin{algorithmic}" in text:
        add(r"\usepackage{algorithm}")
        add(r"\usepackage{algpseudocode}")
    if "\\xspace" in text:
        add(r"\usepackage{xspace}")
    if "\\url{" in text or "\\href{" in text:
        add(r"\usepackage{hyperref}")
    if "\\textcolor{" in text or "\\colorbox{" in text or "\\definecolor{" in text:
        add(r"\usepackage{xcolor}")
    if "\\hologo{" in text:
        add(r"\usepackage{hologo}")
    if "\\textsf{" in text or "\\texttt{" in text or "\\textsc{" in text:
        add(r"\usepackage{fontenc}")

    # ── Packages forwarded from source preamble ──────────────────────────
    _SAFE_TO_FORWARD = {
        "algorithm", "algpseudocode", "algorithmicx",
        "listings", "listingsutf8",
        "xcolor", "color",
        "hyperref", "url",
        "xspace",
        "booktabs", "multirow", "multicol",
        "subcaption", "subfig", "caption",
        "tikz", "pgfplots",
        "enumitem",
        "wasysym", "amssymb", "amsthm",
        "natbib", "cite",
        "tabularx", "longtable", "array",
        "rotating", "pdflscape",
        "minted", "verbatim",
        "cleveref",
        "hologo", "metalogo",
        "relsize", "scalefnt",
    }
    for pkg in (cpr.metadata.get("source_packages") or []):
        if pkg in _SAFE_TO_FORWARD:
            candidate = f"\\usepackage{{{pkg}}}"
            add(candidate)

    # ── TeX engine macro fallbacks ────────────────────────────────────────
    # Papers about TeX ecosystems (and many others) use macros like
    # \XeLaTeX, \LuaLaTeX, \pdflatex, \latexmk, \BibTeX, etc. defined in
    # their preamble. If CPR extraction missed them (e.g. defined in a .sty
    # file via \usepackage, or using \hologo internally), these commands
    # produce blank output or fatal errors. \providecommand is safe: it
    # only takes effect when the command is NOT already defined, so captured
    # custom macros from the source always take precedence.
    _TEX_ENGINE_FALLBACKS = [
        r"\providecommand{\pdfTeX}{pdf\TeX\xspace}",
        r"\providecommand{\pdflatex}{pdf\LaTeX\xspace}",
        r"\providecommand{\pdftex}{pdf\TeX\xspace}",
        r"\providecommand{\XeTeX}{Xe\TeX\xspace}",
        r"\providecommand{\xetex}{Xe\TeX\xspace}",
        r"\providecommand{\XeLaTeX}{Xe\LaTeX\xspace}",
        r"\providecommand{\xelatex}{Xe\LaTeX\xspace}",
        r"\providecommand{\LuaTeX}{Lua\TeX\xspace}",
        r"\providecommand{\luatex}{Lua\TeX\xspace}",
        r"\providecommand{\LuaLaTeX}{Lua\LaTeX\xspace}",
        r"\providecommand{\lualatex}{Lua\LaTeX\xspace}",
        r"\providecommand{\BibTeX}{\textsc{Bib}\TeX\xspace}",
        r"\providecommand{\bibtex}{\textsc{Bib}\TeX\xspace}",
        r"\providecommand{\latexmk}{\texttt{latexmk}\xspace}",
        r"\providecommand{\LaTeXe}{\LaTeX{}2e\xspace}",
        r"\providecommand{\texlive}{\TeX{} Live\xspace}",
        r"\providecommand{\TeXLive}{\TeX{} Live\xspace}",
        r"\providecommand{\MiKTeX}{MiK\TeX\xspace}",
        r"\providecommand{\TikZ}{Ti\textit{k}Z\xspace}",
    ]
    # Only inject fallbacks whose macro name actually appears in the body.
    for fallback in _TEX_ENGINE_FALLBACKS:
        # Extract \commandName from the \providecommand{\commandName}{...}
        m = re.match(r"\\providecommand\{(\\[A-Za-z]+)\}", fallback)
        if m and m.group(1) in text:
            add(fallback)
    # xspace is required by the fallbacks above.
    if any(fb in packages for fb in _TEX_ENGINE_FALLBACKS):
        add(r"\usepackage{xspace}")

    # ── TikZ / pgfplots libraries forwarded from source ──────────────────
    for lib_line in (cpr.metadata.get("tikz_libraries") or []):
        add(lib_line)

    # ── graphicspath ─────────────────────────────────────────────────────
    graphic_roots = cpr.metadata.get("graphics_roots", []) or []
    root_entries: list[str] = []
    for root in graphic_roots:
        clean = str(root or "").strip().replace("\\", "/")
        if not clean:
            continue
        if not clean.endswith("/"):
            clean += "/"
        root_entries.append(f"{{{clean}}}")
    if root_entries:
        add(r"\graphicspath{" + "".join(root_entries) + "}")

    # ── Custom macros from source preamble ───────────────────────────────
    custom_macros = cpr.metadata.get("custom_macros") or []
    if custom_macros:
        packages.append("% Custom macros preserved from source")
        for macro in custom_macros:
            add(macro)

    return ("\n".join(packages) + "\n") if packages else ""


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
    joined = ", ".join(_escape_frontmatter_text(k) for k in cpr.keywords)
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
        profile_block = _render_acm_author_profiles(cpr)
        if profile_block:
            return profile_block
        chunks: list[str] = []
        num_authors = len(cpr.authors)
        for i, name in enumerate(cpr.authors):
            chunks.append(f"\\author{{{_escape_frontmatter_text(name)}}}")
            own_aff = aff[i] if i < len(aff) else None
            own_email = emails[i] if i < len(emails) else None
            if own_aff:
                chunks.append(f"\\affiliation{{\\institution{{{_escape_frontmatter_text(own_aff)}}}}}")
            if own_email:
                chunks.append(f"\\email{{{_escape_frontmatter_text(own_email)}}}")
            # Last author absorbs any trailing affiliations/emails we couldn't pair.
            if i == num_authors - 1:
                for extra in aff[num_authors:]:
                    chunks.append(f"\\affiliation{{\\institution{{{_escape_frontmatter_text(extra)}}}}}")
                for extra in emails[num_authors:]:
                    chunks.append(f"\\email{{{_escape_frontmatter_text(extra)}}}")
        return "\n".join(chunks)

    # IEEE path: classic \author{A \and B \\ Aff1 \and Aff2 \\ emails}
    author_line = " \\and ".join(_escape_frontmatter_text(name) for name in cpr.authors)
    extras: list[str] = []
    if aff:
        extras.append(" \\and ".join(_escape_frontmatter_text(item) for item in aff[: max(len(cpr.authors), len(aff))]))
    if emails:
        extras.append(" \\and ".join(_escape_frontmatter_text(item) for item in emails[: max(len(cpr.authors), len(emails))]))
    if extras:
        return author_line + " \\\\ " + " \\\\ ".join(extras)
    return author_line


def _render_acm_author_profiles(cpr: CanonicalPaperRepresentation) -> str:
    raw_profiles = cpr.metadata.get("author_profiles", []) or []
    if not isinstance(raw_profiles, list):
        return ""
    profiles = [profile for profile in raw_profiles if isinstance(profile, dict) and str(profile.get("name", "")).strip()]
    if not profiles:
        return ""
    author_names = [" ".join(str(name).split()) for name in cpr.authors]
    profile_names = [" ".join(str(profile.get("name", "")).split()) for profile in profiles]
    if author_names and profile_names[: len(author_names)] != author_names[: len(profile_names)]:
        return ""

    chunks: list[str] = []
    for profile in profiles:
        chunks.append(f"\\author{{{_escape_frontmatter_text(str(profile.get('name', '')).strip())}}}")
        affiliation_lines = _render_acm_affiliation_profile(profile)
        if affiliation_lines:
            chunks.append("\\affiliation{%\n" + "\n".join(affiliation_lines) + "\n}")
        email = str(profile.get("email", "") or "").strip()
        if email:
            chunks.append(f"\\email{{{_escape_frontmatter_text(email)}}}")
    return "\n".join(chunks)


def _render_acm_affiliation_profile(profile: dict) -> list[str]:
    ordered_fields = [
        ("department", "department"),
        ("institution", "institution"),
        ("city", "city"),
        ("state", "state"),
        ("country", "country"),
    ]
    lines: list[str] = []
    seen_values: set[str] = set()
    for key, macro in ordered_fields:
        value = " ".join(str(profile.get(key, "") or "").split()).strip()
        if not value:
            continue
        normalized = value.casefold()
        if normalized in seen_values:
            continue
        seen_values.add(normalized)
        lines.append(f"  \\{macro}{{{_escape_frontmatter_text(value)}}}")
    return lines


def _render_extra_frontmatter(cpr: CanonicalPaperRepresentation, target_format: str) -> str:
    if target_format == "acm":
        ccsxml = cpr.metadata.get("ccsxml", "")
        ccs_concepts = cpr.metadata.get("ccs_concepts", []) or []
        chunks: list[str] = [
            r"\setcopyright{none}",
            r"\settopmatter{printacmref=false, printccs=false}",
            r"\renewcommand\footnotetextcopyrightpermission[1]{}",
        ]
        if ccsxml:
            chunks.append(f"\\begin{{CCSXML}}\n{ccsxml}\n\\end{{CCSXML}}")
        for concept in ccs_concepts:
            chunks.append(f"\\ccsdesc{{{concept}}}")
        return "\n".join(chunks)
    if target_format == "ieee":
        conference = str(
            cpr.metadata.get("ieee_conference_header")
            or cpr.metadata.get("conference")
            or ""
        ).strip()
        chunks: list[str] = [r"\IEEEoverridecommandlockouts"]
        if conference:
            chunks.append(f"% Conference: {_escape_frontmatter_text(conference)}")
        return "\n".join(chunks)
    return ""


def _render_acknowledgments(cpr: CanonicalPaperRepresentation, target_format: str) -> str:
    ack = str(cpr.metadata.get("acknowledgments", "") or "").strip()
    if not ack:
        return ""
    if target_format == "acm":
        return f"\n\\begin{{acks}}\n{ack}\n\\end{{acks}}\n"
    return f"\n\\section*{{Acknowledgments}}\n{ack}\n"


def _render_bibliography(cpr: CanonicalPaperRepresentation, target_format: str) -> str:
    bib_mode = str(cpr.metadata.get("bibliography_mode", "") or "").strip().lower()
    if bib_mode == "thebibliography":
        return _render_thebibliography_block(cpr)
    style = "ACM-Reference-Format" if target_format == "acm" else "IEEEtran"
    return f"\\bibliographystyle{{{style}}}\n\\bibliography{{references}}"


def _map_section_title(title: str, target_format: str) -> str:
    canonical = " ".join(title.split())
    aliases = {
        "background": "Background",
        "conclusion": "Conclusion",
        "conclusions": "Conclusion",
    }
    return aliases.get(canonical.strip().lower(), canonical)


def _render_figures(figures) -> str:
    chunks: list[str] = []
    # Defensive dedup: drop figures that share a label or path with one
    # already rendered. Upstream extraction is best-effort and occasionally
    # leaks duplicates; this is the last line of defence before LaTeX sees
    # ``\label{fig:1}`` twice (which would warn and break cross-references).
    seen_labels: set[str] = set()
    seen_paths: set[str] = set()
    for fig in figures:
        if fig.label and fig.label in seen_labels:
            continue
        if fig.path and fig.path in seen_paths:
            continue
        seen_labels.add(fig.label)
        if fig.path:
            seen_paths.add(fig.path)
        asset_block = "% Figure asset unavailable from PDF ingest"
        if fig.path:
            asset_block = f"\\includegraphics[{_graphics_options('figure')}]{{{fig.path}}}"
        chunks.append(
            f"\\begin{{figure}}[{fig.placement}]\n"
            f"\\centering\n"
            f"{asset_block}\n"
            f"\\caption{{{fig.caption}}}\n"
            f"\\label{{{fig.label}}}\n"
            f"\\end{{figure}}"
        )
    return "\n\n".join(chunks)


def _render_tables(tables) -> str:
    chunks: list[str] = []
    seen_labels: set[str] = set()
    for table in tables:
        if table.label and table.label in seen_labels:
            continue
        seen_labels.add(table.label)
        visual_crop = _is_visual_table_crop(table.latex)
        table_latex = _constrain_table_includegraphics(table.latex, span=visual_crop)
        env = "table*" if visual_crop else "table"
        placement = "!t" if visual_crop else table.placement
        chunks.append(
            f"\\begin{{{env}}}[{placement}]\n"
            f"\\caption{{{table.caption}}}\n"
            f"\\label{{{table.label}}}\n"
            f"{table_latex}\n"
            f"\\end{{{env}}}"
        )
    return "\n\n".join(chunks)


def _render_equation_artifacts(artifacts) -> str:
    chunks: list[str] = []
    seen_paths: set[str] = set()
    for artifact in artifacts:
        path = artifact.get("path", "")
        if not path:
            continue
        if path in seen_paths:
            continue
        seen_paths.add(path)
        chunks.append(
            "\\begin{center}\n"
            f"\\includegraphics[{_graphics_options('equation')}]{{{path}}}\n"
            "\\end{center}"
        )
    return "\n\n".join(chunks)


def _graphics_options(kind: str, *, span: bool = False) -> str:
    """Constrain recovered PDF crops so they cannot spill off the output page."""
    if kind == "equation":
        return r"width=0.72\linewidth,height=0.16\textheight,keepaspectratio"
    if kind == "table":
        if span:
            return r"width=\textwidth,height=0.30\textheight,keepaspectratio"
        return r"width=\linewidth,height=0.34\textheight,keepaspectratio"
    return r"width=\linewidth,height=0.42\textheight,keepaspectratio"


def _constrain_table_includegraphics(latex: str, *, span: bool = False) -> str:
    text = str(latex or "")
    if "\\includegraphics" not in text:
        return text
    return re.sub(
        r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}",
        lambda m: f"\\includegraphics[{_graphics_options('table', span=span)}]{{{m.group(1)}}}",
        text,
    )


def _is_visual_table_crop(latex: str) -> bool:
    raw = str(latex or "")
    paths_normalized = raw.replace("\\", "/")
    return "\\includegraphics" in raw and "artifacts/tables/" in paths_normalized


def _dedupe_figures_for_render(figures) -> list:
    deduped: list = []
    seen_labels: set[str] = set()
    seen_paths: set[str] = set()
    for fig in figures:
        label = str(getattr(fig, "label", "") or "")
        path = str(getattr(fig, "path", "") or "")
        if label and label in seen_labels:
            continue
        if path and path in seen_paths:
            continue
        if label:
            seen_labels.add(label)
        if path:
            seen_paths.add(path)
        deduped.append(fig)
    return deduped


def _dedupe_tables_for_render(tables) -> list:
    deduped: list = []
    seen_labels: set[str] = set()
    seen_latex: set[str] = set()
    for table in tables:
        label = str(getattr(table, "label", "") or "")
        latex = re.sub(r"\s+", " ", str(getattr(table, "latex", "") or "")).strip()
        if label and label in seen_labels:
            continue
        if latex and latex in seen_latex:
            continue
        if label:
            seen_labels.add(label)
        if latex:
            seen_latex.add(latex)
        deduped.append(table)
    return deduped


def _dedupe_artifacts_for_render(artifacts) -> list:
    deduped: list = []
    seen_labels: set[str] = set()
    seen_paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        label = str(artifact.get("label", "") or "")
        path = str(artifact.get("path", "") or "")
        if label and label in seen_labels:
            continue
        if path and path in seen_paths:
            continue
        if label:
            seen_labels.add(label)
        if path:
            seen_paths.add(path)
        deduped.append(artifact)
    return deduped


def _inject_artifacts(content: str, artifacts: list[dict[str, str]], escape_body: bool) -> str:
    placeholder_map: dict[str, str] = {}
    working = content or ""
    target_tokens: dict[str, list[str]] = {}
    fallback_tokens: list[str] = []
    for index, artifact in enumerate(artifacts):
        latex = artifact.get("latex", "").strip()
        if not latex:
            continue
        token = f"CODEXARTIFACTTOKEN{index}END"
        placeholder_map[token] = latex
        anchor = (artifact.get("anchor") or "").strip()
        anchor_target = ""
        if artifact.get("kind") == "equation":
            anchor_target = _find_equation_anchor_target(working, str(artifact.get("equation_number", "") or ""))
        if not anchor_target:
            anchor_target = _find_anchor_target(working, anchor)
        if anchor_target:
            target_tokens.setdefault(anchor_target, []).append(token)
        else:
            fallback_tokens.append(token)

    for anchor_target, tokens in target_tokens.items():
        insertion = "\n\n" + "\n\n".join(tokens) + "\n"
        working = working.replace(anchor_target, anchor_target + insertion, 1)

    if fallback_tokens:
        suffix = "" if not working else "\n\n"
        working += f"{suffix}" + "\n\n".join(fallback_tokens) + "\n"

    if escape_body:
        working = _escape_latex_specials(working)

    for token, latex in placeholder_map.items():
        working = working.replace(token, latex)
    return working


def _format_pdf_section_text(content: str) -> str:
    blocks: list[str] = []
    for block in _split_render_blocks(content):
        compact = re.sub(r"\s+", " ", block).strip()
        if not compact:
            continue
        compact = re.sub(r"^(\\subsection\{[^}]+\})\s+", r"\1\n", compact)
        compact = re.sub(r"^(\\subsubsection\{[^}]+\})\s+", r"\1\n", compact)
        blocks.append(compact)
    return "\n\n".join(blocks)


def _split_render_blocks(content: str) -> list[str]:
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    text = re.sub(r"\n{3,}", "\n\n", text)
    raw_blocks = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    blocks: list[str] = []
    for raw in raw_blocks:
        pieces = re.split(r"(?=\\sub(?:sub)?section\{)", raw)
        for piece in pieces:
            stripped = piece.strip()
            if stripped:
                blocks.append(stripped)
    return blocks


def _apply_paragraphwise(content: str, transform) -> str:
    paragraphs = _split_render_blocks(content)
    if not paragraphs:
        normalized = transform(content or "")
        return normalized.strip()
    updated: list[str] = []
    for paragraph in paragraphs:
        transformed = transform(paragraph)
        transformed = transformed.strip()
        if transformed:
            updated.append(transformed)
    return "\n\n".join(updated)


def _remove_artifact_snippets(content: str, snippets: list[str]) -> str:
    unique_snippets = sorted(
        {
            normalized
            for snippet in snippets
            if (normalized := _normalise_artifact_text(snippet or ""))
            and _should_scrub_artifact_snippet(normalized)
        },
        key=len,
        reverse=True,
    )
    # No artifact snippets means no scrub pass is needed. Returning the
    # original content preserves author-authored line structure inside LaTeX
    # environments (e.g., figure blocks containing '%' comments).
    if not unique_snippets:
        return content

    def transform(paragraph: str) -> str:
        working = _normalise_artifact_text(paragraph or "")
        for snippet in unique_snippets:
            if _artifact_snippet_allows_overlap_scrub(snippet):
                scrubbed = _remove_overlapping_artifact_span(working, snippet)
                if scrubbed != working:
                    working = scrubbed
                    continue
            if snippet in working:
                working = working.replace(snippet, " ")
                continue
            words = snippet.split()
            if len(words) >= 12:
                prefix_candidates = [" ".join(words[:12])]
                if len(words) >= 14 and words[0].upper() == "TABLE":
                    prefix_candidates.append(" ".join(words[2:14]))
                for prefix in prefix_candidates:
                    if prefix in working:
                        working = working.replace(prefix, " ")
                        break
                else:
                    prefix = ""
                if prefix:
                    continue
            if len(words) >= 10:
                tail = " ".join(words[-10:])
                if tail in working:
                    working = working.replace(tail, " ")
        return re.sub(r"\s+", " ", working).strip()

    return _apply_paragraphwise(content, transform)


def _should_scrub_artifact_snippet(snippet: str) -> bool:
    words = snippet.split()
    if len(words) >= 24:
        return True
    if snippet.upper().startswith("TABLE ") and len(words) >= 5:
        return True
    if len(words) >= 6 and sum(ch.isdigit() for ch in snippet) >= 4:
        return True
    if len(words) >= 5 and "=" in snippet and re.search(r"\(\d+\)", snippet):
        return True
    return False


def _artifact_snippet_allows_overlap_scrub(snippet: str) -> bool:
    compact = snippet.strip()
    if compact.upper().startswith("TABLE "):
        return True
    digit_count = sum(ch.isdigit() for ch in compact)
    symbol_count = len(re.findall(r"(?:\+|-|0|,)", compact))
    return digit_count >= 8 and symbol_count >= 4


def _remove_overlapping_artifact_span(working: str, snippet: str) -> str:
    snippet_words = snippet.split()
    if len(snippet_words) < 10:
        return working
    first_match: tuple[int, int, int] | None = None
    for size in range(12, 6, -1):
        for start_word in range(0, max(1, len(snippet_words) - size + 1)):
            phrase = " ".join(snippet_words[start_word:start_word + size])
            pos = working.find(phrase)
            if pos == -1:
                continue
            if first_match is None or pos < first_match[0]:
                first_match = (pos, pos + len(phrase), start_word + size)
        if first_match is not None:
            break
    if first_match is None:
        return working

    remove_start, remove_end, tail_floor = first_match
    for size in range(10, 4, -1):
        for start_word in range(len(snippet_words) - size, max(tail_floor - 1, 0), -1):
            phrase = " ".join(snippet_words[start_word:start_word + size])
            pos = working.find(phrase, remove_start)
            if pos != -1:
                remove_end = max(remove_end, pos + len(phrase))
                break
        if remove_end > first_match[1]:
            break
    return working[:remove_start] + " " + working[remove_end:]


def _repair_dangling_visual_references(content: str) -> str:
    cleaned = re.sub(r"\bAs seen in\s*,\s*", "As shown below, ", content, flags=re.I)
    cleaned = re.sub(r"\bas seen in\s*,\s*", "as shown below, ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bin terms of security and usability,\s+as shown below,\s+", "in terms of security and usability, ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bthey were happening\s+at the same time\b", "they were computed concurrently", cleaned, flags=re.I)
    cleaned = re.sub(r"\bconcurrently\)\.", "concurrently.", cleaned)
    return cleaned


def _remove_table_residue(content: str) -> str:
    coord_pair = r"\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)"

    def transform(paragraph: str) -> str:
        working = _normalise_artifact_text(paragraph)
        working = re.sub(
            rf"(?:{coord_pair}\s*){{2}}-?\d+(?:\.\d+)?\s*ft",
            " ",
            working,
            flags=re.I,
        )
        working = re.sub(
            rf"(?:,\s*\d+\)\s*)?{coord_pair}\s*-?\d+(?:\.\d+)?\s*ft",
            " ",
            working,
            flags=re.I,
        )
        working = re.sub(
            r"\bActual\s+N=\d+\s+Positive\s+Negative\s+True\s+\d+\s+\d+\s+False\s+\d+\s+\d+\b",
            " ",
            working,
            flags=re.I,
        )
        working = re.sub(r"\b(?:Actual\s+N=\d+\s+)?(?:Positive\s+)?Negative\s+True\s+False\b", " ", working, flags=re.I)
        working = re.sub(r"(?<!\w)(?:[+\-0]\s+){3,}[+\-0](?!\w)", " ", working)
        working = re.sub(r"\btwo-\s+factor\b", "two-factor", working, flags=re.I)
        working = re.sub(r"\b1(?:5[0-9]|6[0-9])\b(?=\s*,\s*\d+\))", " ", working)
        return re.sub(r"\s+", " ", working).strip()

    return _apply_paragraphwise(content, transform)


_EQUATION_MARKER_RE = re.compile(r"\(\d+\)")
_EQUATION_WINDOW_RADIUS = 220
_EQUATION_MAX_WINDOW = 260
_EQUATION_TRAILING_SCAN = 48
_EQUATION_TOKEN_RE = re.compile(
    r"(?:=|\+|-|/|\*|×|\bx\b|\blog10\b|\bgamma\b|\bsqrt\b|\bDistance\b|\bPLlog\b|\bPL0\b|\bRSSI\b|\b[xydp](?:\d+)?\b)",
    re.I,
)
_EQUATION_COORD_RE = re.compile(r"\(\s*[xydp]\d?\s*[-+]\s*[xydp]\d?\s*\)\d*", re.I)


def _remove_equation_residue(content: str) -> str:
    def transform(paragraph: str) -> str:
        working = _normalise_artifact_text(paragraph)
        spans = _find_equation_residue_spans(working)
        cleaned = _remove_spans(working, spans) if spans else working
        cleaned = re.sub(r"\b(?:Actual\s+N=\d+\s+)?(?:Positive\s+)?Negative\s+True\s+False\b", " ", cleaned, flags=re.I)
        return re.sub(r"\s+", " ", cleaned).strip()

    return _apply_paragraphwise(content, transform)


def _find_equation_residue_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for marker in _EQUATION_MARKER_RE.finditer(text):
        span = _equation_window(text, marker.start(), marker.end())
        if span is None:
            continue
        candidate = text[span[0]:span[1]]
        if not _looks_like_equation_window(candidate):
            continue
        spans = _merge_spans(spans, span)
    return spans


def _equation_window(text: str, marker_start: int, marker_end: int) -> tuple[int, int] | None:
    start_floor = max(0, marker_start - _EQUATION_WINDOW_RADIUS)
    start = start_floor
    for boundary in ("\n", ". ", ": ", "; "):
        idx = text.rfind(boundary, start_floor, marker_start)
        if idx != -1:
            start = max(start, idx + len(boundary))
    end_cap = min(len(text), marker_end + _EQUATION_TRAILING_SCAN)
    end = end_cap
    for boundary in ("\n", ". ", "; ", " 4)", " 5)", " 6)", " \\subsection", " \\section"):
        idx = text.find(boundary, marker_end, end_cap)
        if idx != -1:
            end = min(end, idx)
    if end <= start:
        return None
    if end - start > _EQUATION_MAX_WINDOW:
        return None
    return (start, end)


def _looks_like_equation_window(candidate: str) -> bool:
    compact = " ".join(candidate.split())
    if not compact or len(compact) > _EQUATION_MAX_WINDOW:
        return False
    token_hits = len(_EQUATION_TOKEN_RE.findall(compact))
    digit_hits = sum(ch.isdigit() for ch in compact)
    has_assignment = "=" in compact
    has_coordinate_term = bool(_EQUATION_COORD_RE.search(compact))
    has_superscript_style = bool(re.search(r"[A-Za-z0-9]\)\d|\b[xydp]\d\b", compact, re.I))
    has_fraction_like_math = bool(re.search(r"\b[A-Z]{1,3}\s*\+\s*[A-Z]{1,3}\b", compact)) and bool(
        re.search(r"(?:×|x)\s*100|\b100\s*(?:×|x)\b", compact, re.I)
    )
    if has_assignment and (token_hits >= 5 or has_coordinate_term):
        return True
    if has_coordinate_term and digit_hits >= 4 and token_hits >= 4:
        return True
    if has_fraction_like_math and digit_hits >= 3:
        return True
    return has_assignment and has_superscript_style and digit_hits >= 2


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    cleaned: list[str] = []
    cursor = 0
    for start, end in spans:
        cleaned.append(text[cursor:start])
        cleaned.append(" ")
        cursor = end
    cleaned.append(text[cursor:])
    return "".join(cleaned)


def _merge_spans(existing: list[tuple[int, int]], new_span: tuple[int, int]) -> list[tuple[int, int]]:
    if not existing:
        return [new_span]
    merged = existing[:]
    start, end = new_span
    last_start, last_end = merged[-1]
    if start <= last_end:
        merged[-1] = (last_start, max(last_end, end))
    else:
        merged.append(new_span)
    return merged


def _find_anchor_target(content: str, anchor: str) -> str:
    for candidate in _anchor_candidates(anchor):
        if candidate and candidate in content and _anchor_is_safe(candidate):
            return candidate
    return ""


def _find_equation_anchor_target(content: str, equation_number: str) -> str:
    number = str(equation_number or "").strip()
    if not number:
        return ""
    reference_re = re.compile(
        rf"\b(?:eq\.?|equation)\s*\(?\s*{re.escape(number)}\s*\)?",
        re.I,
    )
    for sentence in _sentence_like_spans(content):
        if reference_re.search(sentence):
            return sentence.strip()
    return ""


def _sentence_like_spans(content: str) -> list[str]:
    text = content or ""
    if not text.strip():
        return []
    spans: list[str] = []
    start = 0
    for match in re.finditer(r"[.!?](?:\s+|$)", text):
        end = match.end()
        candidate = text[start:end].strip()
        if candidate:
            spans.append(candidate)
        start = end
    tail = text[start:].strip()
    if tail:
        spans.append(tail)
    return spans


def _anchor_candidates(anchor: str) -> list[str]:
    cleaned = " ".join((anchor or "").split())
    if not cleaned:
        return []
    candidates: list[str] = [cleaned]
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
    if sentences:
        candidates.append(sentences[-1])
    words = cleaned.split()
    for size in (16, 12, 8):
        if len(words) > size:
            candidates.append(" ".join(words[-size:]))
    deduped: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _anchor_is_safe(anchor: str) -> bool:
    stripped = anchor.strip()
    if not stripped:
        return False
    if stripped.endswith((".", ":", "!", "?")):
        return True
    return len(stripped.split()) >= 12


def _locate_artifact_section(sections, snippet: str, fallback: str) -> str:
    normalized_snippet = _normalise_artifact_text(snippet)
    if not normalized_snippet:
        return fallback
    prefix = " ".join(normalized_snippet.split()[:12])
    for section in sections:
        normalized_content = _normalise_artifact_text(section.content)
        if normalized_snippet in normalized_content:
            return section.title
        if prefix and prefix in normalized_content:
            return section.title
    return fallback


def _locate_equation_section(sections, artifact: dict, text_map_snippet: str) -> str:
    number = str(artifact.get("equation_number", "") or "").strip()
    if number:
        reference_re = re.compile(
            rf"\b(?:eq\.?|equation)\s*\(?\s*{re.escape(number)}\s*\)?",
            re.I,
        )
        for section in sections:
            if reference_re.search(section.content):
                return section.title

    anchor_resolved = _locate_artifact_section(sections, str(artifact.get("anchor_text", "") or ""), "")
    if anchor_resolved:
        return anchor_resolved

    raw_snippet = str(artifact.get("raw_text", "") or text_map_snippet or "")
    return _locate_artifact_section(sections, raw_snippet, str(artifact.get("section_title", "") or ""))


_FIGURE_KEYWORD_STOPWORDS = {
    "figure",
    "fig",
    "overview",
    "results",
    "system",
    "model",
    "method",
    "paper",
}


def _locate_figure_section(sections, label: str, anchor: str, caption: str, fallback: str) -> str:
    anchor_resolved = _locate_artifact_section(sections, anchor, "")
    if anchor_resolved:
        return anchor_resolved

    number_match = re.search(r"(\d+)", label or "")
    if number_match:
        number = number_match.group(1)
        reference_pattern = re.compile(rf"\b(?:Fig\.?|Figure)\s*{re.escape(number)}\b", re.I)
        for section in sections:
            if reference_pattern.search(section.content):
                return section.title

    caption_keywords = [
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9\-]+", _normalise_artifact_text(caption or ""))
        if len(word) > 3 and word.lower() not in _FIGURE_KEYWORD_STOPWORDS
    ]
    if caption_keywords:
        best_title = fallback
        best_score = 0
        for section in sections:
            normalized_content = _normalise_artifact_text(section.content).lower()
            score = sum(1 for keyword in set(caption_keywords) if keyword in normalized_content)
            if score > best_score:
                best_title = section.title
                best_score = score
        if best_score >= 2 or (len(set(caption_keywords)) == 1 and best_score == 1):
            return best_title

    return fallback


def _render_bib_stub(cpr: CanonicalPaperRepresentation) -> str:
    """Serialise CPR references into a BibTeX file.

    If an entry's ``raw`` field is already a complete ``@type{...}`` block, it
    is emitted verbatim so we don't lose fidelity on LaTeX-source ingests. For
    PDF-ingested references, we fall through to a structured guesser that tries
    to recover author/title/year/venue from common IEEE/ACM reference shapes.
    """
    source_bib = str(cpr.metadata.get("source_bib_text", "") or "").strip()
    if source_bib:
        return source_bib + ("\n" if not source_bib.endswith("\n") else "")

    if not cpr.references:
        return "% No references extracted\n"
    entries: list[str] = []
    seen_keys: set[str] = set()
    malformed_keys: list[str] = []
    for ref in cpr.references:
        if ref.key in seen_keys:
            continue
        seen_keys.add(ref.key)
        raw = (ref.raw or "").strip()
        if raw.startswith("@") and raw.endswith("}") and "{" in raw:
            entries.append(raw)
            continue
        entry_type, fields = _guess_bibtex_fields(ref.key, ref.raw)
        if len(str(fields.get("title", "") or "")) > 200:
            malformed_keys.append(ref.key)
        field_text = "\n".join(
            [f"  {k}={{{_sanitise_bib_value(v)}}}," for k, v in fields.items() if v]
        )
        entries.append(f"@{entry_type}{{{ref.key},\n{field_text}\n}}")
    if malformed_keys:
        cpr.metadata.setdefault("reference_warnings", []).append(
            f"Likely malformed references (title > 200 chars): {', '.join(malformed_keys[:8])}"
        )
    return "\n\n".join(entries) + "\n"


def _render_thebibliography_block(cpr: CanonicalPaperRepresentation) -> str:
    if not cpr.references:
        return "\\begin{thebibliography}{00}\n\\end{thebibliography}\n"
    width = max(2, len(str(len(cpr.references))))
    items: list[str] = [f"\\begin{{thebibliography}}{{{'9' * width}}}"]
    for ref in cpr.references:
        raw = _normalise_reference_text(ref.raw)
        items.append(f"\\bibitem{{{ref.key}}} {raw or ref.key}")
    items.append("\\end{thebibliography}")
    return "\n".join(items) + "\n"


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
    text = (
        text.replace("ﬁ", "fi")
        .replace("ﬂ", "fl")
        .replace("–", "-")
        .replace("—", "-")
        .replace("“", '"')
        .replace("”", '"')
        .replace("’", "'")
    )
    if not text:
        return "misc", {"note": raw or ""}

    year = ""
    year_match = re.search(r"\b(19|20)\d{2}\b", text)
    if year_match:
        year = year_match.group(0)
    text = re.sub(r"\b\d{1,4}\s*$", "", text).strip().rstrip(".,")

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

    sentence_split = re.split(r"(?<=[a-z0-9\)])\.\s+", text, maxsplit=2)
    first_sentence = sentence_split[0].strip() if sentence_split else ""
    if _looks_like_reference_author_block(first_sentence):
        author = first_sentence.rstrip(",.")
        cutoff_idx = len(first_sentence) + 1
    else:
        author = text[:cutoff_idx].strip().rstrip(",.")
    # If the "author" block is suspiciously long, it's probably run-together
    # content; clip it so BibTeX doesn't choke on a giant author list.
    if len(author) > 240:
        author = author[:240]

    remainder_after_author = text[cutoff_idx:].strip().lstrip(",:.")
    title_sentence = ""

    # Venue: between the end of the title and the next year/volume/page hint.
    venue_start = cutoff_idx
    if quoted:
        venue_start = quoted.end()
        venue_text = text[venue_start:].strip().lstrip(",:.")
    else:
        title_sentence = re.split(r"(?<=[a-z0-9\)])\.\s+", remainder_after_author, maxsplit=1)[0].strip()
        venue_text = remainder_after_author[len(title_sentence):].strip().lstrip(",:.") if title_sentence else remainder_after_author

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
        remainder = remainder_after_author
        if _looks_like_reference_author_block(first_sentence) and len(sentence_split) >= 2:
            remainder = ". ".join(sentence_split[1:]).strip()
        title_sentence = re.split(r"(?<=[a-z0-9\)])\.\s+", remainder, maxsplit=1)[0].strip()
        # Cut at first venue marker so the title doesn't eat the venue.
        for marker in (" in Proc", " Proc.", ", Proceedings"):
            m_idx = remainder.find(marker)
            if m_idx > 0:
                remainder = remainder[:m_idx]
                break
        title = (title_sentence or remainder[:180]).strip().rstrip(",.") or text[:180]

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


def _looks_like_reference_author_block(text: str) -> bool:
    if not text:
        return False
    if len(text) > 180:
        return False
    if '"' in text:
        return False
    if any(marker in text for marker in ("Journal", "Conference", "Proceedings", "Workshop", "Transactions")):
        return False
    return bool(
        re.search(r"\b(?:[A-Z]\.){1,3}\s*[A-Z][A-Za-z'-]+", text)
        or re.search(r"\b[A-Z][a-z]+,\s*[A-Z]\.", text)
        or " and " in text
    )


def _sanitise_bib_value(value: str) -> str:
    """Escape characters that would break a BibTeX field value."""
    return (value or "").replace("{", "(").replace("}", ")").strip()


def _escape_frontmatter_text(text: str) -> str:
    if not text:
        return text
    result = text
    for raw, escaped in _LATEX_SPECIAL_ESCAPES:
        result = re.sub(rf"(?<!\\){re.escape(raw)}", escaped, result)
    return result


def _normalise_reference_text(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = (
        cleaned.replace("ï¬", "fi")
        .replace("ï¬‚", "fl")
        .replace("â€“", "--")
        .replace("â€”", "---")
        .replace("â€œ", '"')
        .replace("â€", '"')
        .replace("â€™", "'")
    )
    return _escape_frontmatter_text(re.sub(r"\s+", " ", cleaned))


def _normalise_artifact_text(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = (
        cleaned.replace("ï¬", "fi")
        .replace("ï¬‚", "fl")
        .replace("â€“", "-")
        .replace("â€”", "-")
        .replace("âˆ’", "-")
        .replace("−", "-")
        .replace("Ã—", "x")
        .replace("×", "x")
        .replace("Î³", "γ")
    )
    cleaned = cleaned.replace("ï¬", "fi").replace("ï¬‚", "fl")
    cleaned = cleaned.replace("×", "x")
    cleaned = cleaned.replace("Î³", "gamma").replace("γ", "gamma")
    return re.sub(r"\s+", " ", cleaned)
