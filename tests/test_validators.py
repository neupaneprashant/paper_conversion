from __future__ import annotations

from pathlib import Path

from paper_conversion_system.models import CanonicalPaperRepresentation, Section
from paper_conversion_system.validators import validate_project


def _write_project(tmp_path: Path, body: str) -> tuple[Path, CanonicalPaperRepresentation]:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    main = project_dir / "main.tex"
    main.write_text(body, encoding="utf-8")
    cpr = CanonicalPaperRepresentation(
        title="T",
        authors=["A"],
        abstract="x",
        keywords=["k"],
        sections=[Section(title="Introduction", content="hello"), Section(title="Conclusion", content="bye")],
        references=[],
        metadata={},
    )
    return project_dir, cpr


def test_validate_flags_acm_ceremony_leaking_into_ieee_target(tmp_path: Path) -> None:
    body = (
        r"\documentclass[conference]{IEEEtran}"
        "\n\\begin{document}\n"
        r"\setcopyright{acmlicensed}"
        "\n"
        r"\acmDOI{10.1145/x}"
        "\n"
        r"\acmJournal{TOIS}"
        "\n"
        r"\authornote{leaked}"
        "\n"
        r"\title{T}\maketitle"
        "\n"
        r"\section{Introduction}hello"
        "\n"
        r"\section{Conclusion}bye"
        "\n\\end{document}\n"
    )
    project_dir, cpr = _write_project(tmp_path, body)
    summary = validate_project(project_dir, cpr, "ieee")
    text = " | ".join(summary.warnings)
    # Each of these ACM commands must be flagged as a discouraged source-venue pattern
    for needle in (r"\setcopyright", r"\acmDOI", r"\acmJournal", r"\authornote"):
        assert needle in text, f"validator did not flag leaked {needle}: {text}"


def test_validate_flags_ieee_only_macros_leaking_into_acm_target(tmp_path: Path) -> None:
    body = (
        r"\documentclass[sigconf]{acmart}"
        "\n\\begin{document}\n"
        r"\IEEEoverridecommandlockouts"
        "\n"
        r"\title{T}\author{A}\maketitle"
        "\n"
        r"\IEEEPARstart{T}{his} paper"
        "\n"
        r"\section{Introduction}hello"
        "\n"
        r"\section{Conclusion}bye"
        "\n\\end{document}\n"
    )
    project_dir, cpr = _write_project(tmp_path, body)
    summary = validate_project(project_dir, cpr, "acm")
    text = " | ".join(summary.warnings)
    for needle in (r"\IEEEoverridecommandlockouts", r"\IEEEPARstart"):
        assert needle in text, f"validator did not flag leaked {needle}: {text}"


def test_validate_does_not_false_positive_when_leakage_absent(tmp_path: Path) -> None:
    """Clean IEEE output should not trip any FORBIDDEN_OUTPUT_PATTERNS warnings."""
    body = (
        r"\documentclass[conference]{IEEEtran}"
        "\n\\begin{document}\n"
        r"\title{T}\author{A}\maketitle"
        "\n"
        r"\section{Introduction}hello"
        "\n"
        r"\section{Conclusion}bye"
        "\n\\end{document}\n"
    )
    project_dir, cpr = _write_project(tmp_path, body)
    summary = validate_project(project_dir, cpr, "ieee")
    leakage_msgs = [w for w in summary.warnings if "discouraged source-venue pattern" in w]
    assert leakage_msgs == []
