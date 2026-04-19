from __future__ import annotations

from .models import CanonicalPaperRepresentation, Section


def normalize_cpr_for_target(cpr: CanonicalPaperRepresentation, target_format: str) -> tuple[CanonicalPaperRepresentation, dict]:
    warnings: list[str] = []
    assumptions: list[str] = []

    cpr.title = " ".join(cpr.title.split())
    cpr.authors = [_clean_author_name(a) for a in cpr.authors if a.strip()]
    cpr.keywords = [_clean_keyword(k) for k in cpr.keywords if k.strip()]
    cpr.sections = _normalize_sections(cpr.sections)

    source_format = str(cpr.metadata.get("source_format", "")).lower()

    if target_format == "acm":
        if source_format == "ieee" and cpr.keywords:
            assumptions.append("Converted IEEEkeywords to ACM keywords")
        if cpr.metadata.get("ccs_concepts") and not cpr.metadata.get("ccsxml"):
            warnings.append("CCS concepts metadata incomplete; ACM CCS block omitted")
    elif target_format == "ieee":
        if cpr.metadata.get("ccs_concepts"):
            warnings.append("ACM CCS concepts have no strong IEEE-native equivalent; omitted from IEEE output")
        if cpr.metadata.get("acknowledgments"):
            assumptions.append("Acknowledgments routed to IEEE-compatible unnumbered section fallback")

    if not cpr.authors:
        warnings.append("No author metadata extracted; output may require manual frontmatter completion")
    if not cpr.abstract:
        warnings.append("No abstract extracted from source")
    if not cpr.references:
        warnings.append("No bibliography entries extracted; reference output may be incomplete")

    cpr.metadata["normalization_warnings"] = warnings
    cpr.metadata["normalization_assumptions"] = assumptions
    return cpr, {"warnings": warnings, "assumptions": assumptions}


def _normalize_sections(sections: list[Section]) -> list[Section]:
    normalized: list[Section] = []
    for section in sections:
        title = " ".join(section.title.split())
        content = section.content.strip()
        normalized.append(Section(title=title, content=content))
    return normalized


def _clean_author_name(name: str) -> str:
    return " ".join(name.replace("\n", " ").split())


def _clean_keyword(keyword: str) -> str:
    return " ".join(keyword.replace(";", ",").split())
