from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable

from .cpr import brace_scan, parse_project_to_cpr, strip_balanced_command
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
    r"|\\acmBooktitle"
    r"|\\acmDOI"
    r"|\\setcopyright"
    r"|\\acmISBN"
    r"|\\acmPrice"
    r"|\\acmJournal"
    r"|\\acmVolume"
    r"|\\acmNumber"
    r"|\\acmArticle"
    r"|\\acmYear"
    r"|\\authornote"
    r"|\\authorsaddresses"
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
    _active_notes: list[str]

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

        self._active_notes = []

        if isinstance(source, CanonicalPaperRepresentation):
            # PDF ingest or alternate parsers hand us CPR directly.
            cpr = source
            cpr.metadata.setdefault("source_format", self.source_format)
        else:
            # Run raw-source guardrails *before* spending time on a full parse.
            pre_warnings.extend(self._check_source_format(source))
            cpr = parse_project_to_cpr(source, self.source_format)

        self._agent_preprocess(cpr, source)

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
            warnings=pre_warnings + list(norm["warnings"]) + list(self._active_notes),
            assumptions=list(norm["assumptions"]) + list(cpr.metadata.get("agent_assumptions", []) or []),
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

    def _agent_preprocess(
        self,
        cpr: CanonicalPaperRepresentation,
        source: Path | CanonicalPaperRepresentation,
    ) -> None:
        """Agent-specific preprocessing before shared normalization."""
        return


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

    def _agent_preprocess(
        self,
        cpr: CanonicalPaperRepresentation,
        source: Path | CanonicalPaperRepresentation,
    ) -> None:
        # Prefer source LaTeX for IEEEauthorblock parsing.
        source_latex = str(cpr.metadata.get("source_latex_expanded", "") or "")
        if not source_latex:
            return
        profiles = _parse_ieee_author_profiles(source_latex)
        if not profiles:
            return
        cpr.metadata["author_profiles"] = profiles
        cpr.authors = [p["name"] for p in profiles if p.get("name")]
        cpr.metadata["emails"] = [p.get("email", "") for p in profiles if p.get("email")]
        cpr.metadata["affiliations"] = [p.get("institution", "") for p in profiles if p.get("institution")]
        cpr.metadata.setdefault("agent_assumptions", []).append(
            "April parsed IEEEauthorblockN/A into ACM author/affiliation/email profiles."
        )
        self._active_notes.append(
            "April: translated IEEEauthorblockN/A frontmatter into structured ACM author profiles."
        )

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

    def _agent_preprocess(
        self,
        cpr: CanonicalPaperRepresentation,
        source: Path | CanonicalPaperRepresentation,
    ) -> None:
        # Strip ACM ceremony from preserved preamble to avoid leaking into IEEE output.
        preamble = str(cpr.metadata.get("source_preamble", "") or "")
        if preamble:
            cleaned, removed = _strip_acm_ceremony_macros(preamble)
            if removed:
                cpr.metadata["source_preamble"] = cleaned
                cpr.metadata["acm_ceremony_removed"] = removed
                self._active_notes.append(
                    f"Friday: stripped ACM ceremony macros from source preamble ({', '.join(removed)})."
                )
        # Promote conference metadata for IEEE header rendering.
        conf = str(cpr.metadata.get("conference", "") or "").strip()
        if conf:
            cpr.metadata["ieee_conference_header"] = conf
            cpr.metadata.setdefault("agent_assumptions", []).append(
                "Friday mapped ACM conference metadata into IEEE conference header hint."
            )

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

    Images are also mirrored at root **only when their basename is unique**
    across the whole project — if two subdirectories each contain a file named
    ``img.pdf``, mirroring one would silently overwrite the other and break
    ``\\includegraphics{img}`` references.  In conflict cases we skip the
    mirror and rely on ``\\graphicspath`` for resolution instead.
    """
    if not isinstance(source, Path) or not source.exists():
        return
    source_root = source if source.is_dir() else source.parent

    _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg"}
    _SKIP_EXTS = {".aux", ".bbl", ".blg", ".fdb_latexmk", ".fls", ".log", ".out", ".synctex.gz", ".toc"}

    # Pre-scan: build basename → [source files] map to detect conflicts.
    basename_count: dict[str, int] = {}
    for child in source_root.rglob("*"):
        if child.is_file() and child.suffix.lower() in _IMAGE_EXTS:
            basename_count[child.name] = basename_count.get(child.name, 0) + 1
    # Only mirror image basenames that appear exactly once in the project.
    unique_image_basenames: set[str] = {name for name, count in basename_count.items() if count == 1}

    mirrored_basenames: set[str] = set()
    for child in source_root.rglob("*"):
        if not child.is_file():
            continue
        if any(part.startswith(".") for part in child.relative_to(source_root).parts):
            continue
        if child.suffix.lower() in _SKIP_EXTS:
            continue
        rel = child.relative_to(source_root)
        if rel == Path("main.tex"):
            continue
        dest = output_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child, dest)
        if child.suffix.lower() in _IMAGE_EXTS:
            rel_parent = rel.parent.as_posix()
            if rel_parent and rel_parent != "." and child.name in unique_image_basenames:
                # Only mirror images whose basename is unambiguous project-wide.
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


_IEEE_AUTHOR_N_TRIGGER = re.compile(r"\\IEEEauthorblockN\s*\{", re.I)
_IEEE_AUTHOR_A_TRIGGER = re.compile(r"\\IEEEauthorblockA\s*\{", re.I)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TEXTIT_RE = re.compile(r"\\textit\{([^}]*)\}")


def _scan_brace_groups(text: str, trigger: re.Pattern[str]) -> list[str]:
    """Yield brace-balanced bodies for every match of *trigger* in *text*.

    The trigger regex MUST end at the opening ``{`` of the group it points to.
    Bodies may contain nested commands like ``\\textit{...}`` or ``\\href{...}``;
    the regex-only ``[^}]*`` form trips on those.
    """
    groups: list[str] = []
    pos = 0
    while pos < len(text):
        m = trigger.search(text, pos)
        if m is None:
            break
        open_idx = m.end() - 1  # the '{' character itself
        end = brace_scan(text, open_idx)
        if end == open_idx:
            # Unbalanced — bail out cleanly so we don't infinite-loop.
            break
        groups.append(text[open_idx + 1: end - 1])
        pos = end
    return groups


def _parse_ieee_author_profiles(latex: str) -> list[dict[str, str]]:
    """Parse IEEEauthorblockN/A groups into per-author profile dictionaries."""
    names = [g.strip() for g in _scan_brace_groups(latex, _IEEE_AUTHOR_N_TRIGGER)]
    affs = [g.strip() for g in _scan_brace_groups(latex, _IEEE_AUTHOR_A_TRIGGER)]
    if not names or not affs:
        return []

    groups = min(len(names), len(affs))
    profiles: list[dict[str, str]] = []
    for idx in range(groups):
        raw_names = _split_names(names[idx])
        aff_block = affs[idx]
        clean_aff = _clean_affiliation_block(aff_block)
        emails = _EMAIL_RE.findall(aff_block)
        for i, name in enumerate(raw_names):
            profile = {
                "name": name,
                "department": "",
                "institution": clean_aff,
                "city": "",
                "state": "",
                "country": "",
                "email": emails[i] if i < len(emails) else (emails[0] if emails else ""),
            }
            profiles.append(profile)
    return profiles


def _split_names(raw: str) -> list[str]:
    # IEEE blocks may use commas and/or \and.
    chunks = re.split(r"\\and|,", raw)
    return [" ".join(c.split()) for c in chunks if c.strip()]


def _clean_affiliation_block(text: str) -> str:
    cleaned = _TEXTIT_RE.sub(r"\1", text or "")
    cleaned = re.sub(r"\\\\", ", ", cleaned)
    cleaned = _EMAIL_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    return cleaned


# ACM ceremony / metadata macros that must NOT leak into IEEE output.
# Each entry is (label, command, arg_count). Optional bracket arguments like
# ``\acmConference[short]{...}{...}{...}`` are stripped via the prefix scan.
_ACM_CEREMONY_MACROS: list[tuple[str, str, int]] = [
    # Copyright / licensing ceremony
    ("setcopyright",       r"\setcopyright",       1),
    ("copyrightyear",      r"\copyrightyear",      1),
    ("acmYear",            r"\acmYear",            1),
    # Conference / venue
    ("acmConference",      r"\acmConference",      3),
    ("acmBooktitle",       r"\acmBooktitle",       1),
    # Identifiers
    ("acmDOI",             r"\acmDOI",             1),
    ("acmISBN",            r"\acmISBN",            1),
    ("acmPrice",           r"\acmPrice",           1),
    # Journal-mode metadata (acmart journal templates)
    ("acmJournal",         r"\acmJournal",         1),
    ("acmVolume",          r"\acmVolume",          1),
    ("acmNumber",          r"\acmNumber",          1),
    ("acmArticle",         r"\acmArticle",         1),
    ("acmMonth",           r"\acmMonth",           1),
    ("acmArticleSeq",      r"\acmArticleSeq",      1),
    ("acmSubmissionID",    r"\acmSubmissionID",    1),
    # Editorial / review state
    ("received",           r"\received",           1),
    ("editor",             r"\editor",             1),
    ("authornote",         r"\authornote",         1),
    ("authorsaddresses",   r"\authorsaddresses",   1),
]


def _strip_acm_ceremony_macros(text: str) -> tuple[str, list[str]]:
    """Strip ACM-only ceremony commands using brace-balanced scanning.

    The previous regex-only approach (``\\cmd\\{[^}]*\\}``) silently failed when
    a metadata argument contained nested commands such as
    ``\\acmDOI{\\href{...}{...}}`` or ``\\acmConference[short]{The Foo'25 \\& Bar
    Conference}{...}{...}``.  Delegates to the shared brace-aware command
    scanner in :mod:`cpr` so nested groups are respected.
    """
    cleaned = text
    removed: list[str] = []
    for label, command, arg_count in _ACM_CEREMONY_MACROS:
        new_cleaned, count = strip_balanced_command(cleaned, command, arg_count)
        if count:
            removed.append(label)
            cleaned = new_cleaned
    return cleaned, removed


# Backwards-compat alias for tests/legacy callers that imported the private name.
_strip_balanced_command = strip_balanced_command
