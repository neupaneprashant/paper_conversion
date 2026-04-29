from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable

from .cpr import parse_project_to_cpr
from .models import CanonicalPaperRepresentation, ConversionReport
from .normalization import normalize_cpr_for_target
from .render import render_cpr_to_target


# ---------------------------------------------------------------------------
# Format-detection patterns
# ---------------------------------------------------------------------------

# Document-class fingerprints
_IEEE_DOCCLASS_RE = re.compile(
    r"\\documentclass(?:\[[^\]]*\])?\{IEEEtran\}", re.IGNORECASE
)
_ACM_DOCCLASS_RE = re.compile(
    r"\\documentclass(?:\[[^\]]*\])?\{acmart\}", re.IGNORECASE
)

# Structural markers unique to each venue
_IEEE_MARKERS_RE = re.compile(
    r"\\IEEEauthorblock[NA]"
    r"|\\begin\{IEEEkeywords\}"
    r"|\\IEEEpeerreviewmaketitle"
    r"|\\IEEEtriggeratref"
    r"|\\begin\{IEEEbiography\}",
    re.IGNORECASE,
)
_ACM_MARKERS_RE = re.compile(
    r"\\begin\{CCSXML\}"
    r"|\\ccsdesc"
    r"|\\acmConference"
    r"|\\acmDOI"
    r"|\\setcopyright"
    r"|\\acmISBN"
    r"|\\acmPrice"
    r"|\\received",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Shared base agent
# ---------------------------------------------------------------------------

class _ConversionAgent:
    """Shared conversion contract for the two venue-specialist agents.

    Design intent:
    - keep April/Friday thin and declarative
    - centralise the common CPR -> normalisation -> rendering flow
    - ensure both conversion directions return the same report shape
    - guard against wrong-format inputs before committing to a full parse

    Each subclass declares source/target intent, format-detection patterns,
    and venue-specific unresolved-item rules.
    """

    name: str = ""
    source_format: str = ""
    target_format: str = ""
    target_template_profile: str = ""

    # Regex that SHOULD match the source document class (e.g. IEEEtran).
    _expected_class_re: re.Pattern | None = None
    # Regex whose match signals the *wrong* format was supplied.
    _wrong_format_re: re.Pattern | None = None
    # Regex for venue-specific command markers used in format detection.
    _wrong_markers_re: re.Pattern | None = None

    def convert(
        self,
        source: Path | CanonicalPaperRepresentation,
        output_dir: Path,
        stage_callback: Callable[[str], None] | None = None,
    ) -> tuple[Path, ConversionReport, CanonicalPaperRepresentation]:
        """Run the end-to-end conversion flow for one agent direction.

        Accepted inputs:
        - a source project path  (normal LaTeX-first path)
        - a CPR object           (used by PDF ingest and alternate parsers)

        Returns:
        - converted project directory
        - structured conversion report
        - normalised CPR used to generate the output
        """
        pre_warnings: list[str] = []

        if isinstance(source, CanonicalPaperRepresentation):
            # PDF ingest or alternate parsers hand us CPR directly.
            cpr = source
            cpr.metadata.setdefault("source_format", self.source_format)
        else:
            # Run raw-source guardrails *before* spending time on a full parse.
            pre_warnings.extend(self._check_source_format(source))
            cpr = parse_project_to_cpr(source, self.source_format)

        if stage_callback is not None:
            stage_callback("normalize")
        cpr, norm = normalize_cpr_for_target(cpr, self.target_format)

        if isinstance(source, Path):
            cpr.metadata["graphics_roots"] = _discover_graphics_roots(source)

        if stage_callback is not None:
            stage_callback("render")
        main_path = render_cpr_to_target(cpr, self.target_format, output_dir)

        # Preserve side assets (figures, bibliography files, style files).
        _copy_assets_if_any(source, output_dir)
        # Ensure \bibliography{references} always resolves.
        _ensure_references_bib_alias(source, output_dir)

        report = ConversionReport(
            mapped_fields=[
                "title", "authors", "abstract", "keywords",
                "sections", "figures", "tables", "equations",
                "references", "acknowledgments",
            ],
            changed_sections=[s.title for s in cpr.sections],
            unresolved_items=self._collect_unresolved(cpr),
            warnings=pre_warnings + list(norm["warnings"]),
            assumptions=list(norm["assumptions"]),
            target_template_profile=self.target_template_profile,
            source_format=self.source_format,
            target_format=self.target_format,
            ingest_confidence=cpr.metadata.get("ingest_confidence"),
        )
        return main_path.parent, report, cpr

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------

    def _check_source_format(self, source: Path) -> list[str]:
        """Scan raw LaTeX to detect format mismatches before parsing.

        Returns a list of warning strings (empty means all clear).
        Warnings are non-fatal — conversion proceeds so partial output
        is still generated, but the caller is informed of the anomaly.
        """
        warnings: list[str] = []
        if not source.exists():
            return warnings

        main_tex = _find_main_tex(source)
        if main_tex is None:
            warnings.append(
                f"No main .tex entry point found under {source}; "
                "conversion may produce empty output."
            )
            return warnings

        try:
            text = main_tex.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return warnings

        # Detect wrong document class.
        if self._wrong_format_re and self._wrong_format_re.search(text):
            warnings.append(
                f"[{self.name}] Input looks like {self.target_format.upper()} "
                f"(found its \\documentclass), but {self.name} expects "
                f"{self.source_format.upper()} source. Output may be unreliable — "
                "verify the Source Format selector."
            )

        # Detect wrong-venue structural markers even if the docclass is missing.
        elif self._wrong_markers_re and self._wrong_markers_re.search(text):
            warnings.append(
                f"[{self.name}] Found {self.target_format.upper()}-specific commands "
                f"in the source, but {self.name} expects {self.source_format.upper()} input. "
                "Check the Source Format selector."
            )

        # Warn if the expected document class is absent (could be a custom wrapper).
        if self._expected_class_re and not self._expected_class_re.search(text):
            warnings.append(
                f"[{self.name}] Expected \\documentclass{{...{self.source_format}...}} "
                "not found. The source may be a custom template — review the output carefully."
            )

        return warnings

    # ------------------------------------------------------------------
    # Unresolved-item collection (overridden per agent)
    # ------------------------------------------------------------------

    def _collect_unresolved(self, cpr: CanonicalPaperRepresentation) -> list[str]:
        """Return items that need human review after conversion."""
        return []


# ---------------------------------------------------------------------------
# April — IEEE → ACM
# ---------------------------------------------------------------------------

class April(_ConversionAgent):
    """April converts IEEE manuscripts into ACM-compliant LaTeX.

    Guardrails:
    - Rejects (with a warning) ACM \\documentclass or ACM-only commands in source.
    - Flags missing ACM-required metadata (CCS concepts, affiliations, conference).
    - Warns on IEEE-specific structures with no direct ACM equivalent.
    - Flags content-scale issues that affect ACM two-column layout.
    """

    name = "April"
    source_format = "ieee"
    target_format = "acm"
    target_template_profile = "acmart-sigconf"

    _expected_class_re = _IEEE_DOCCLASS_RE
    _wrong_format_re = _ACM_DOCCLASS_RE
    _wrong_markers_re = _ACM_MARKERS_RE

    def _collect_unresolved(self, cpr: CanonicalPaperRepresentation) -> list[str]:
        unresolved: list[str] = []

        # ── Frontmatter completeness ─────────────────────────────────────
        if not cpr.title.strip():
            unresolved.append(
                "Title missing — \\title{} will be empty in ACM output; add manually."
            )
        if not cpr.abstract.strip():
            unresolved.append(
                "Abstract not extracted — \\begin{abstract} block will be empty."
            )
        if not cpr.authors:
            unresolved.append(
                "No authors detected — ACM \\author/\\affiliation blocks will be absent."
            )

        # ── ACM-required metadata ────────────────────────────────────────
        if cpr.metadata.get("ccs_concepts") and not cpr.metadata.get("ccsxml"):
            unresolved.append(
                "CCS concepts detected but no full CCSXML block found; "
                "add \\begin{CCSXML}...\\end{CCSXML} and \\ccsdesc manually "
                "(use https://dl.acm.org/ccs)."
            )
        if not cpr.metadata.get("emails") and not cpr.metadata.get("affiliations"):
            unresolved.append(
                "No author affiliations or e-mails inferred; "
                "ACM \\affiliation{} and \\email{} blocks will be incomplete — fill in manually."
            )
        if not cpr.metadata.get("conference"):
            unresolved.append(
                "No conference metadata found; \\acmConference{} will use placeholder values — "
                "update with the correct venue, year, and location."
            )
        if not cpr.keywords:
            unresolved.append(
                "No keywords detected; \\keywords{} will be empty — add ACM-style keywords manually."
            )

        # ── IEEE-specific structures without ACM equivalents ─────────────
        body_text = " ".join(s.content for s in cpr.sections)
        if re.search(r"\\IEEEauthorblock[NA]", body_text, re.IGNORECASE):
            unresolved.append(
                "IEEE author blocks (\\IEEEauthorblockN / \\IEEEauthorblockA) found in body — "
                "verify these were converted to ACM \\author / \\affiliation correctly."
            )
        if any(
            s.title.lower() in {"biography", "biographies", "about the authors"}
            for s in cpr.sections
        ):
            unresolved.append(
                "IEEE biography section detected; ACM sigconf does not use author biographies — "
                "section will be omitted or converted to an appendix."
            )
        if re.search(r"\\IEEEpeerreviewmaketitle", body_text, re.IGNORECASE):
            unresolved.append(
                "\\IEEEpeerreviewmaketitle found — IEEE peer-review title page has no ACM equivalent; removed."
            )

        # ── Content scale / layout ───────────────────────────────────────
        if len(cpr.equations) > 20:
            unresolved.append(
                f"Paper contains {len(cpr.equations)} equations — "
                "verify ACM two-column equation rendering and \\[ ... \\] numbering."
            )
        if len(cpr.figures) > 10:
            unresolved.append(
                f"Paper contains {len(cpr.figures)} figures — "
                "check \\begin{{figure*}} (span both columns) vs \\begin{{figure}} placement "
                "in ACM layout."
            )
        if len(cpr.tables) > 5:
            unresolved.append(
                f"Paper contains {len(cpr.tables)} tables — "
                "verify booktabs / ACM table style compatibility."
            )
        if not cpr.references:
            unresolved.append(
                "No references extracted — bibliography may be missing or unparseable; "
                "check references.bib in the output."
            )

        return unresolved


# ---------------------------------------------------------------------------
# Friday — ACM → IEEE
# ---------------------------------------------------------------------------

class Friday(_ConversionAgent):
    """Friday converts ACM manuscripts into IEEE-compliant LaTeX.

    Guardrails:
    - Rejects (with a warning) IEEE \\documentclass or IEEE-only commands in source.
    - Reports all ACM-only metadata fields stripped from IEEE output.
    - Flags content-scale issues that affect IEEEtran two-column layout.
    - Warns when the bibliography is missing or empty.
    """

    name = "Friday"
    source_format = "acm"
    target_format = "ieee"
    target_template_profile = "IEEEtran-conference"

    _expected_class_re = _ACM_DOCCLASS_RE
    _wrong_format_re = _IEEE_DOCCLASS_RE
    _wrong_markers_re = _IEEE_MARKERS_RE

    def _collect_unresolved(self, cpr: CanonicalPaperRepresentation) -> list[str]:
        unresolved: list[str] = []

        # ── Frontmatter completeness ─────────────────────────────────────
        if not cpr.title.strip():
            unresolved.append(
                "Title missing — \\title{} will be empty in IEEE output; add manually."
            )
        if not cpr.abstract.strip():
            unresolved.append(
                "Abstract not extracted — \\begin{abstract} block will be empty."
            )
        if not cpr.authors:
            unresolved.append(
                "No authors detected — IEEE \\author block will be absent."
            )

        # ── ACM metadata stripped from IEEE output ───────────────────────
        if cpr.metadata.get("ccs_concepts"):
            unresolved.append(
                "ACM CCS concepts stripped from IEEE output "
                "(IEEEtran does not use CCS taxonomy)."
            )
        _acm_only: dict[str, str] = {
            "acm_doi":       "\\acmDOI",
            "acm_isbn":      "\\acmISBN",
            "acm_price":     "\\acmPrice",
            "copyright_year": "copyright-year metadata",
            "received":      "\\received dates",
        }
        stripped = [label for key, label in _acm_only.items() if cpr.metadata.get(key)]
        if stripped:
            unresolved.append(
                f"ACM-only metadata stripped from IEEE output: {', '.join(stripped)}."
            )

        # ── Conference / venue metadata ──────────────────────────────────
        if not cpr.metadata.get("conference"):
            unresolved.append(
                "No conference metadata found; IEEE header will use placeholder venue — "
                "update \\IEEEoverridecommandlockouts / \\def\\BibTeX with correct venue."
            )

        # ── ACM-specific section types ───────────────────────────────────
        _acm_section_names = {"ccs concepts", "keywords"}
        present = [
            s.title for s in cpr.sections
            if s.title.lower() in _acm_section_names
        ]
        if present:
            unresolved.append(
                f"ACM-specific sections converted to IEEE equivalents: {', '.join(present)}."
            )

        # ── Content scale / layout ───────────────────────────────────────
        if len(cpr.figures) > 15:
            unresolved.append(
                f"Paper contains {len(cpr.figures)} figures — "
                "verify IEEE two-column figure placement "
                "(\\begin{{figure*}} spans both columns)."
            )
        if len(cpr.equations) > 20:
            unresolved.append(
                f"Paper contains {len(cpr.equations)} equations — "
                "verify IEEEtran equation numbering and \\[ ... \\] rendering."
            )
        if len(cpr.tables) > 5:
            unresolved.append(
                f"Paper contains {len(cpr.tables)} tables — "
                "check IEEEtran table style and column-span usage."
            )

        # ── Bibliography ─────────────────────────────────────────────────
        if not cpr.references:
            unresolved.append(
                "No references extracted — bibliography may be missing or unparseable; "
                "check references.bib in the output."
            )

        return unresolved


# ---------------------------------------------------------------------------
# Asset helpers
# ---------------------------------------------------------------------------

def _copy_assets_if_any(source: Path | CanonicalPaperRepresentation, output_dir: Path) -> None:
    """Copy source-project support files into the converted project.

    We preserve relative paths for nested figure folders, style files, class
    files, bibliography databases, and other project artifacts so the rendered
    target can still resolve ``\\includegraphics`` and template dependencies.
    ``main.tex`` is always left alone so we don't overwrite the generated output.
    """
    if not isinstance(source, Path) or not source.exists():
        return
    source_root = source if source.is_dir() else source.parent
    copied_image_dirs: set[str] = set()
    mirrored_basenames: set[str] = set()
    for child in source_root.rglob("*"):
        if not child.is_file():
            continue
        if any(part.startswith(".") for part in child.relative_to(source_root).parts):
            continue
        if child.suffix.lower() in {
            ".aux", ".bbl", ".blg", ".fdb_latexmk",
            ".fls", ".log", ".out", ".synctex.gz", ".toc",
        }:
            continue
        rel = child.relative_to(source_root)
        if rel == Path("main.tex"):
            continue
        dest = output_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child, dest)
        if child.suffix.lower() in {".png", ".jpg", ".jpeg", ".pdf", ".eps"}:
            rel_parent = rel.parent.as_posix()
            if rel_parent and rel_parent != ".":
                copied_image_dirs.add(rel_parent)
            # Mirror by basename at root so \includegraphics{file.png} resolves
            # when source projects rely on \graphicspath shortcuts.
            if child.name not in mirrored_basenames:
                flat_dest = output_dir / child.name
                if not flat_dest.exists():
                    shutil.copy2(child, flat_dest)
                mirrored_basenames.add(child.name)


def _ensure_references_bib_alias(
    source: Path | CanonicalPaperRepresentation,
    output_dir: Path,
) -> None:
    """Make sure output_dir/references.bib contains real bibliography data.

    The LaTeX templates hardcode \\bibliography{references}.  If the source
    project uses a differently-named .bib file (e.g. paper.bib, main.bib),
    bibtex cannot find references.bib and the References section is empty.

    If references.bib in the output is still just the small CPR stub
    (< 200 bytes is a reliable heuristic), find the largest real .bib file
    in the source and copy it over.
    """
    if not isinstance(source, Path) or not source.exists():
        return
    ref_dest = output_dir / "references.bib"
    if ref_dest.exists() and ref_dest.stat().st_size > 200:
        return  # already has meaningful content
    source_root = source if source.is_dir() else source.parent
    best: Path | None = None
    best_size = 0
    for bib in source_root.rglob("*.bib"):
        if not bib.is_file():
            continue
        sz = bib.stat().st_size
        if sz > best_size:
            best_size = sz
            best = bib
    if best is not None:
        shutil.copy2(best, ref_dest)


def _discover_graphics_roots(source: Path) -> list[str]:
    """Return all subdirectory paths that contain image files."""
    if not source.exists():
        return []
    source_root = source if source.is_dir() else source.parent
    roots: set[str] = set()
    for child in source_root.rglob("*"):
        if not child.is_file():
            continue
        if child.suffix.lower() not in {".png", ".jpg", ".jpeg", ".pdf", ".eps"}:
            continue
        rel_parent = child.relative_to(source_root).parent.as_posix()
        if rel_parent and rel_parent != ".":
            roots.add(f"./{rel_parent}")
    # Include root as fallback for flattened basename mirrors.
    roots.add("./")
    return sorted(roots)


def _find_main_tex(source: Path) -> Path | None:
    """Locate the main .tex entry point for a project path.

    Checks in order:
    1. The path itself if it is a .tex file.
    2. main.tex directly inside a directory.
    3. Any .tex file containing \\begin{document}.
    4. The first .tex file found anywhere under the directory.
    """
    if source.is_file() and source.suffix.lower() == ".tex":
        return source
    if not source.is_dir():
        return None
    direct = next(source.glob("main.tex"), None)
    if direct is not None:
        return direct
    for candidate in source.glob("*.tex"):
        try:
            if "\\begin{document}" in candidate.read_text(encoding="utf-8", errors="ignore"):
                return candidate
        except Exception:
            continue
    return next(source.rglob("*.tex"), None)
