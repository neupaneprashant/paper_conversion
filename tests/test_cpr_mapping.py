from pathlib import Path

from paper_conversion_system.cpr import parse_project_to_cpr
from paper_conversion_system.render import render_cpr_to_target


def test_ieee_to_cpr_mapping(tmp_path: Path):
    src = tmp_path / "ieee"
    src.mkdir()
    (src / "main.tex").write_text(
        r"""
\documentclass[conference]{IEEEtran}
\title{A Study}
\author{Alice, Bob}
\begin{document}
\maketitle
\begin{abstract}Abstract text\end{abstract}
\begin{IEEEkeywords}systems, agents\end{IEEEkeywords}
\section{Introduction}
Hello world.
\bibliography{references}
\end{document}
""",
        encoding="utf-8",
    )
    (src / "references.bib").write_text("@article{ref1, title={X}}", encoding="utf-8")
    cpr = parse_project_to_cpr(src, "ieee")
    assert cpr.title == "A Study"
    assert cpr.authors == ["Alice", "Bob"]
    assert cpr.abstract == "Abstract text"
    assert cpr.keywords == ["systems", "agents"]
    assert cpr.sections[0].title == "Introduction"
    assert cpr.references[0].key == "ref1"


def test_acm_render_from_cpr(tmp_path: Path):
    src = tmp_path / "ieee"
    src.mkdir()
    (src / "main.tex").write_text(
        r"""
\documentclass[conference]{IEEEtran}
\title{A Study}
\author{Alice}
\begin{document}
\begin{abstract}Abstract text\end{abstract}
\section{Introduction}
Hello world.
\end{document}
""",
        encoding="utf-8",
    )
    cpr = parse_project_to_cpr(src, "ieee")
    out = tmp_path / "out"
    render_cpr_to_target(cpr, "acm", out)
    text = (out / "main.tex").read_text(encoding="utf-8")
    assert "\\documentclass[sigconf]{acmart}" in text
    assert "\\section{Introduction}" in text
