from pathlib import Path

from paper_conversion_system.orchestrator import route_and_run


def test_citation_and_figure_mapping_regression(tmp_path: Path):
    sample = tmp_path / "ieee_sample"
    sample.mkdir()
    (sample / "main.tex").write_text(
        r"""
\documentclass[conference]{IEEEtran}
\title{Regression}
\author{Alice}
\begin{document}
\begin{abstract}Abstract text\end{abstract}
\section{Introduction}
See Figure~\ref{fig:one}. \cite{r1}
\begin{figure}[tbp]\caption{Cap}\label{fig:one}\end{figure}
\bibliography{references}
\end{document}
""",
        encoding="utf-8",
    )
    (sample / "references.bib").write_text("@article{r1, title={Demo}}", encoding="utf-8")
    result = route_and_run("ieee", "acm", sample, tmp_path / "job")
    converted = Path(result.converted_source_path) / "main.tex"
    text = converted.read_text(encoding="utf-8")
    assert "\\cite{r1}" in text
    assert "\\ref{fig:one}" in text


def test_unsupported_direction_rejected(tmp_path: Path):
    sample = tmp_path / "dummy"
    sample.mkdir()
    (sample / "main.tex").write_text("\\begin{document}x\\end{document}", encoding="utf-8")
    try:
        route_and_run("ieee", "ieee", sample, tmp_path / "job")
    except ValueError as exc:
        assert "Unsupported conversion direction" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unsupported direction")
