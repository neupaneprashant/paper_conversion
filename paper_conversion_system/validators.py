from __future__ import annotations

from pathlib import Path
import re

from .models import CanonicalPaperRepresentation, ValidationSummary


# Required sections are warn-level only. We don't fail conversion on missing
# sections because PDF ingest frequently produces a single "Body" section that
# is semantically fine but doesn't match the canonical skeleton.
REQUIRED_SECTIONS = {
    "ieee": ["Introduction"],
    "acm": ["Introduction"],
}

# Expected sections we strongly prefer to see in a paper-style conversion.
# Missing these surfaces as a warning, not a failure.
EXPECTED_SECTIONS = {
    "ieee": ["Introduction", "Conclusion"],
    "acm": ["Introduction", "Conclusion"],
}

# Patterns that should not leak through to the target format. For example, the
# ACM output should never contain IEEEtran-specific environments, and the IEEE
# output should never contain ACM-only macros.
FORBIDDEN_OUTPUT_PATTERNS = {
    "ieee": [
        # ACM document class / package
        r"\\documentclass\[[^\]]*\]\{acmart\}",
        # ACM-only structural environments
        r"\\begin\{CCSXML\}",
        r"\\begin\{acks\}",
        r"\\ccsdesc",
        # ACM ceremony / metadata commands (must be stripped by Friday)
        r"\\setcopyright\b",
        r"\\copyrightyear\b",
        r"\\acmYear\b",
        r"\\acmConference\b",
        r"\\acmBooktitle\b",
        r"\\acmDOI\b",
        r"\\acmISBN\b",
        r"\\acmPrice\b",
        r"\\acmJournal\b",
        r"\\acmVolume\b",
        r"\\acmNumber\b",
        r"\\acmArticle\b",
        r"\\acmMonth\b",
        r"\\acmArticleSeq\b",
        r"\\acmSubmissionID\b",
        r"\\authornote\b",
        r"\\authorsaddresses\b",
        r"\\received\b",
    ],
    "acm": [
        # IEEE document class / package
        r"\\documentclass\[[^\]]*\]\{IEEEtran\}",
        # IEEE-only structural environments
        r"\\begin\{IEEEkeywords\}",
        r"\\begin\{IEEEbiography\}",
        # IEEE-only macros (must be stripped by April)
        r"\\IEEEPARstart\b",
        r"\\IEEEauthorblockN\b",
        r"\\IEEEauthorblockA\b",
        r"\\IEEEoverridecommandlockouts\b",
        r"\\IEEEpeerreviewmaketitle\b",
        r"\\IEEEpubid\b",
        r"\\IEEEtriggeratref\b",
    ],
}


def validate_project(project_dir: Path, cpr: CanonicalPaperRepresentation, target_format: str) -> ValidationSummary:
    """Run layered structural and target-format validation on a converted project.

    Validation is organised roughly along the layers described in SPEC.md:
        L1  syntax / file-presence sanity
        L2  template/frontmatter compliance (required environments + no leakage)
        L3  cross-reference integrity (unresolved \\ref targets)

    Severity rules:
        errors     -> things that will almost certainly break the target template
                      or indicate the conversion itself is malformed
        warnings   -> things a reviewer should look at but don't fail the build
    """
    summary = ValidationSummary()
    main = project_dir / "main.tex"

    # L1 - basic presence.
    if not main.exists():
        summary.errors.append("main.tex missing")
        summary.template_compliance = "fail"
        return summary

    text = main.read_text(encoding="utf-8", errors="ignore")

    # L1 - document skeleton sanity.
    skeleton_errors = _check_skeleton(text, target_format)
    summary.errors.extend(skeleton_errors)

    # L2 - required + expected section compliance (warn-level).
    missing_required = _missing_sections(cpr, REQUIRED_SECTIONS.get(target_format, []))
    missing_expected = _missing_sections(cpr, EXPECTED_SECTIONS.get(target_format, []))
    has_any_body = any(len(s.content.strip()) > 0 for s in cpr.sections)

    if missing_required and not has_any_body:
        # No body at all and no required sections -> the conversion is basically empty.
        summary.template_compliance = "fail"
        summary.errors.append(
            f"Missing required sections and no body content: {', '.join(missing_required)}"
        )
    elif missing_required:
        summary.template_compliance = "warn"
        summary.warnings.append(
            f"Required sections not found (using body content as-is): {', '.join(missing_required)}"
        )
    else:
        summary.template_compliance = "pass"

    expected_gaps = [s for s in missing_expected if s not in missing_required]
    if expected_gaps:
        summary.warnings.append(
            f"Expected sections missing: {', '.join(expected_gaps)}"
        )

    # L2 - citation/bibliography presence.
    summary.citation_compliance = _check_citation_compliance(text, cpr, summary)
    for warning in cpr.metadata.get("reference_warnings", []) or []:
        summary.warnings.append(str(warning))
    _warn_malformed_references(cpr, summary)

    # L2 - forbidden (leaked) patterns for the target template.
    for pattern in FORBIDDEN_OUTPUT_PATTERNS.get(target_format, []):
        if re.search(pattern, text):
            summary.warnings.append(
                f"Target format '{target_format}' contains discouraged source-venue pattern: {pattern}"
            )

    # L2 - target-format frontmatter sanity.
    if target_format == "ieee":
        if "\\documentclass" in text and "IEEEtran" not in text:
            summary.warnings.append("Target is IEEE but \\documentclass does not reference IEEEtran")
    elif target_format == "acm":
        if "\\documentclass" in text and "acmart" not in text:
            summary.warnings.append("Target is ACM but \\documentclass does not reference acmart")

    # L3 - unresolved cross references.
    unresolved_fig_refs = _unresolved_refs(text, "fig:")
    unresolved_tbl_refs = _unresolved_refs(text, "tab:")
    if unresolved_fig_refs:
        summary.warnings.append(f"Potential unresolved figure refs: {unresolved_fig_refs}")
    if unresolved_tbl_refs:
        summary.warnings.append(f"Potential unresolved table refs: {unresolved_tbl_refs}")

    # L1 - detect dangling LaTeX commands that often indicate truncated output.
    if re.search(r"\\[A-Za-z]+\s*$", text, re.M) and "\\end{document}" not in text:
        summary.warnings.append("Potential broken LaTeX command at line end")

    # L1 - document must terminate properly.
    if "\\end{document}" not in text:
        summary.errors.append("main.tex has no \\end{document} terminator")
        summary.template_compliance = "fail"

    # L1 - document must have a \begin{document}.
    if "\\begin{document}" not in text:
        summary.errors.append("main.tex has no \\begin{document}")
        summary.template_compliance = "fail"

    return summary


def _missing_sections(cpr: CanonicalPaperRepresentation, required: list[str]) -> list[str]:
    present = {s.title.strip().lower() for s in cpr.sections}
    return [s for s in required if s.lower() not in present]


def _unresolved_refs(text: str, prefix: str) -> list[str]:
    refs = re.findall(r"\\ref\{([^}]+)\}", text)
    labels = set(re.findall(r"\\label\{([^}]+)\}", text))
    return sorted({r for r in refs if r.startswith(prefix) and r not in labels})


def _check_skeleton(text: str, target_format: str) -> list[str]:
    """Catch obviously malformed output early so Comp doesn't attempt to compile it."""
    errors: list[str] = []
    if text.count("\\end{document}") > 1:
        errors.append("Multiple \\end{document} blocks detected")
    if text.count("\\begin{document}") > 1:
        errors.append("Multiple \\begin{document} blocks detected")
    # Running \title{} more than once is a frequent symptom of a template merge bug.
    if len(re.findall(r"\\title\{", text)) > 1:
        errors.append("Multiple \\title{...} declarations detected")
    return errors


def _check_citation_compliance(text: str, cpr: CanonicalPaperRepresentation, summary: ValidationSummary) -> str:
    """Determine citation compliance level and append any relevant warnings."""
    has_cite = "\\cite{" in text
    has_bib = "\\bibliography{" in text or "\\printbibliography" in text or "\\begin{thebibliography}" in text
    has_bib_style = "\\bibliographystyle{" in text or "\\printbibliography" in text or "\\begin{thebibliography}" in text

    if not has_bib:
        summary.warnings.append("No \\bibliography{...}, \\printbibliography, or thebibliography detected")
        return "warn"
    if has_bib and not has_bib_style and "\\printbibliography" not in text:
        summary.warnings.append("\\bibliography present but no \\bibliographystyle detected")
    if not has_cite:
        summary.warnings.append("No \\cite{...} calls detected; references may be unused")
        return "warn"

    # If we have \cite keys, check that at least some resolve against CPR.references.
    if cpr.references:
        cite_keys = set(re.findall(r"\\cite\{([^}]+)\}", text))
        cite_keys = {k.strip() for group in cite_keys for k in group.split(",")}
        known_keys = {ref.key for ref in cpr.references}
        unresolved = sorted(cite_keys - known_keys)
        if unresolved and len(unresolved) == len(cite_keys):
            summary.warnings.append(
                f"All \\cite keys appear unresolved against bibliography: {unresolved[:5]}"
            )
            return "warn"
        if unresolved:
            summary.warnings.append(
                f"Some \\cite keys not found in bibliography: {unresolved[:5]}"
            )

    return "pass"


def _warn_malformed_references(cpr: CanonicalPaperRepresentation, summary: ValidationSummary) -> None:
    suspicious: list[str] = []
    for ref in cpr.references:
        raw = str(ref.raw or "")
        if len(raw) > 500:
            suspicious.append(ref.key)
    if suspicious:
        summary.warnings.append(
            f"Reference entries may be malformed or over-merged (raw length > 500): {suspicious[:8]}"
        )
