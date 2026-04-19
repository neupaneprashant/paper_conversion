from __future__ import annotations

from pathlib import Path
import shutil

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
        cpr, norm = normalize_cpr_for_target(cpr, self.target_format)
        main_path = render_cpr_to_target(cpr, self.target_format, output_dir)
        # Preserve side assets such as figures and bibliography files when present.
        _copy_assets_if_any(source, output_dir)

        report = ConversionReport(
            mapped_fields=[
                "title", "authors", "abstract", "keywords",
                "sections", "references", "acknowledgments",
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
    """Copy common side assets from a source project into the converted project.

    This is intentionally conservative: it only copies known paper assets such as
    images, PDF figures, EPS files, and bibliography files.
    """
    if not isinstance(source, Path) or not source.exists() or not source.is_dir():
        return
    for child in source.iterdir():
        if child.is_file() and child.suffix.lower() in {".png", ".jpg", ".jpeg", ".pdf", ".eps", ".bib"}:
            shutil.copy2(child, output_dir / child.name)
