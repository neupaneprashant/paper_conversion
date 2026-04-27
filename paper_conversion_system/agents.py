from __future__ import annotations

from pathlib import Path
import shutil
from typing import Callable

from .cpr import parse_project_to_cpr
from .models import CanonicalPaperRepresentation, ConversionReport
from .normalization import normalize_cpr_for_target
from .render import render_cpr_to_target


class _ConversionAgent:
    """Shared conversion contract for the two venue-specialist agents.

    Design intent:
    - keep April/Friday thin and declarative
    - centralize the common CPR -> normalization -> rendering flow
    - ensure both conversion directions return the same report shape

    Each subclass only declares source/target intent and unresolved-item rules.
    """

    name: str = ""
    source_format: str = ""
    target_format: str = ""
    target_template_profile: str = ""

    def convert(
        self,
        source: Path | CanonicalPaperRepresentation,
        output_dir: Path,
        stage_callback: Callable[[str], None] | None = None,
    ) -> tuple[Path, ConversionReport, CanonicalPaperRepresentation]:
        """Run the end-to-end conversion flow for one agent direction.

        Accepted inputs:
        - a source project path (normal LaTeX-first path)
        - a CPR object (used by PDF ingest and alternate parsers)

        Returns:
        - converted project directory
        - structured conversion report
        - normalized CPR used to generate the output
        """
        if isinstance(source, CanonicalPaperRepresentation):
            # PDF ingest or alternate parsers can hand us CPR directly.
            cpr = source
            # CPR originating from PDF ingest may lack a source_format hint; set one.
            cpr.metadata.setdefault("source_format", self.source_format)
        else:
            # Standard path for source LaTeX projects.
            cpr = parse_project_to_cpr(source, self.source_format)

        # Normalize before rendering so target-specific cleanup is shared.
        if stage_callback is not None:
            stage_callback("normalize")
        cpr, norm = normalize_cpr_for_target(cpr, self.target_format)
        if stage_callback is not None:
            stage_callback("render")
        main_path = render_cpr_to_target(cpr, self.target_format, output_dir)
        # Preserve side assets such as figures and bibliography files when present.
        _copy_assets_if_any(source, output_dir)

        report = ConversionReport(
            mapped_fields=[
                "title", "authors", "abstract", "keywords",
                "sections", "figures", "tables", "equations",
                "references", "acknowledgments",
            ],
            changed_sections=[s.title for s in cpr.sections],
            unresolved_items=self._collect_unresolved(cpr),
            warnings=list(norm["warnings"]),
            assumptions=list(norm["assumptions"]),
            target_template_profile=self.target_template_profile,
            source_format=self.source_format,
            target_format=self.target_format,
            ingest_confidence=cpr.metadata.get("ingest_confidence"),
        )
        return main_path.parent, report, cpr

    def _collect_unresolved(self, cpr: CanonicalPaperRepresentation) -> list[str]:
        return []


class April(_ConversionAgent):
    """April converts IEEE manuscripts into ACM-compliant LaTeX."""

    name = "April"
    source_format = "ieee"
    target_format = "acm"
    target_template_profile = "acmart-sigconf"

    def _collect_unresolved(self, cpr: CanonicalPaperRepresentation) -> list[str]:
        unresolved: list[str] = []
        if cpr.metadata.get("ccs_concepts") and not cpr.metadata.get("ccsxml"):
            unresolved.append("CCS concepts detected without full ACM CCSXML block")
        if not cpr.metadata.get("emails") and not cpr.metadata.get("affiliations"):
            unresolved.append("No ACM author frontmatter (affiliations/emails) inferred")
        return unresolved


class Friday(_ConversionAgent):
    """Friday converts ACM manuscripts into IEEE-compliant LaTeX."""

    name = "Friday"
    source_format = "acm"
    target_format = "ieee"
    target_template_profile = "IEEEtran-conference"

    def _collect_unresolved(self, cpr: CanonicalPaperRepresentation) -> list[str]:
        unresolved: list[str] = []
        if cpr.metadata.get("ccs_concepts"):
            unresolved.append("ACM CCS concepts omitted from IEEE output")
        return unresolved


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
    for child in source_root.rglob("*"):
        if not child.is_file():
            continue
        if any(part.startswith(".") for part in child.relative_to(source_root).parts):
            continue
        if child.suffix.lower() in {".aux", ".bbl", ".blg", ".fdb_latexmk", ".fls", ".log", ".out", ".synctex.gz", ".toc"}:
            continue
        rel = child.relative_to(source_root)
        if rel == Path("main.tex"):
            continue
        dest = output_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child, dest)
