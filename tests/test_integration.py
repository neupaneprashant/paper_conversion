from pathlib import Path

from paper_conversion_system.orchestrator import route_and_run


def _make_ieee_sample(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.tex").write_text(
        r"""
\documentclass[conference]{IEEEtran}
\title{IEEE Sample}
\author{Alice, Bob}
\begin{document}
\maketitle
\begin{abstract}Abstract text\end{abstract}
\begin{IEEEkeywords}latex, conversion\end{IEEEkeywords}
\section{Introduction}
See Figure~\ref{fig:one}. \cite{ref1}
\begin{figure}[tbp]\caption{Demo}\label{fig:one}\end{figure}
\bibliography{references}
\end{document}
""",
        encoding="utf-8",
    )
    (root / "references.bib").write_text("@article{ref1, title={Demo}}", encoding="utf-8")
    return root


def _make_acm_sample(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.tex").write_text(
        r"""
\documentclass[sigconf]{acmart}
\title{ACM Sample}
\author{Carol}
\begin{document}
\begin{abstract}Abstract text\end{abstract}
\keywords{latex, conversion}
\maketitle
\section{Introduction}
See Table~\ref{tab:one}. \cite{ref1}
\begin{table}[tbp]\caption{Demo}\label{tab:one}\end{table}
\bibliography{references}
\end{document}
""",
        encoding="utf-8",
    )
    (root / "references.bib").write_text("@article{ref1, title={Demo}}", encoding="utf-8")
    return root


def test_ieee_to_acm_integration(tmp_path: Path):
    sample = _make_ieee_sample(tmp_path / "ieee_sample")
    result = route_and_run("ieee", "acm", sample, tmp_path / "job1")
    assert result.direction == "ieee_to_acm"
    assert result.converted_source_path is not None
    assert result.validation.template_compliance in {"pass", "fail"}
    assert "conversion_report" in result.reports


def test_acm_to_ieee_integration(tmp_path: Path):
    sample = _make_acm_sample(tmp_path / "acm_sample")
    result = route_and_run("acm", "ieee", sample, tmp_path / "job2")
    assert result.direction == "acm_to_ieee"
    assert result.converted_source_path is not None
    assert result.validation.template_compliance in {"pass", "fail"}
    assert "compile_report" in result.reports
