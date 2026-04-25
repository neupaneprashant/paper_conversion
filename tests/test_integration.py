from pathlib import Path

import fitz

from paper_conversion_system.orchestrator import route_and_run


_ONE_BY_ONE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf"
    b"\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


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


def _make_asset_heavy_sample(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sections").mkdir()
    (root / "figs").mkdir()
    (root / "main.tex").write_text(
        r"""
\documentclass[sigconf]{acmart}
\title{Asset Sample}
\author{Casey}
\begin{document}
\begin{abstract}Abstract text\end{abstract}
\maketitle
\input{sections/intro}
\bibliography{references}
\end{document}
""",
        encoding="utf-8",
    )
    (root / "sections" / "intro.tex").write_text(
        r"""
\section{Introduction}
See Figure~\ref{fig:asset} and Table~\ref{tab:asset}. \cite{ref1}
\begin{figure}[tbp]
\centering
\includegraphics[width=\linewidth]{figs/example.png}
\caption{Nested asset}
\label{fig:asset}
\end{figure}
\begin{table}[tbp]
\caption{Nested table}
\label{tab:asset}
\begin{tabular}{ll}
A & B \\
1 & 2 \\
\end{tabular}
\end{table}
""",
        encoding="utf-8",
    )
    (root / "figs" / "example.png").write_bytes(_ONE_BY_ONE_PNG)
    (root / "references.bib").write_text("@article{ref1, title={Demo}}", encoding="utf-8")
    return root


def _make_pdf_with_embedded_figure(pdf_path: Path) -> Path:
    image_path = pdf_path.with_suffix(".png")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 220, 160), False)
    pix.set_rect(pix.irect, (255, 0, 0))
    pix.save(str(image_path))

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(
        (72, 72),
        "Secure Digital Signature Validated by Ambient Users\n"
        "Abstract\n"
        "This abstract describes the paper content and provides enough words to be parsed.\n"
        "I. INTRODUCTION\n"
        "This section explains the method and references Figure 1 for the overview.\n"
        "Figure 1. Overview validation entity with ambient devices and RSSI workflow.\n"
        "REFERENCES\n"
        "[1] Demo reference entry for regression coverage.",
        fontsize=12,
    )
    page.insert_image(fitz.Rect(72, 220, 292, 380), filename=str(image_path))
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def _make_pdf_with_vector_figure(pdf_path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(
        (72, 72),
        "Vector Figure Paper\n"
        "Abstract\n"
        "This abstract describes the paper content and provides enough words to be parsed.\n"
        "I. INTRODUCTION\n"
        "This section explains the method and references Figure 1 for the overview.",
        fontsize=12,
    )
    page.draw_rect(fitz.Rect(90, 190, 230, 260), color=(0, 0, 0), width=1.5)
    page.draw_line(fitz.Point(230, 225), fitz.Point(310, 225), color=(0, 0, 0), width=1.5)
    page.draw_rect(fitz.Rect(310, 190, 450, 260), color=(0, 0, 0), width=1.5)
    page.insert_text((118, 222), "Client", fontsize=11)
    page.insert_text((346, 222), "Server", fontsize=11)
    page.insert_text((144, 282), "Fig. 1. Vector overview for regression coverage", fontsize=10)
    page.insert_text(
        (72, 330),
        "REFERENCES\n"
        "[1] Demo reference entry for regression coverage.",
        fontsize=12,
    )
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def _make_pdf_with_unnumbered_sections(pdf_path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(
        (72, 72),
        "Ambient Validation for Secure Signatures\n"
        "Abstract\n"
        "This abstract summarizes the method and includes enough prose to exercise PDF ingest.\n"
        "Keywords\n"
        "digital signature, validation, wi-fi\n"
        "Introduction\n"
        "This section explains the approach in enough detail to survive section extraction and mentions Figure 1 in passing.\n"
        "Literature Review Categories\n"
        "This section surveys prior work and adds enough body text to remain distinct from the introduction section.\n"
        "REFERENCES\n"
        "[1] A. Author, Demo reference entry, 2024.\n"
        "[2] B. Author, Another reference entry, 2023.",
        fontsize=12,
    )
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


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


def test_assets_and_included_sections_preserved(tmp_path: Path):
    sample = _make_asset_heavy_sample(tmp_path / "asset_sample")
    result = route_and_run("acm", "ieee", sample, tmp_path / "job3")
    converted_dir = Path(result.converted_source_path)
    converted_main = (converted_dir / "main.tex").read_text(encoding="utf-8")
    assert "\\includegraphics[width=\\linewidth]{figs/example.png}" in converted_main
    assert "\\label{fig:asset}" in converted_main
    assert "\\label{tab:asset}" in converted_main
    assert "\\begin{tabular}{ll}" in converted_main
    assert (converted_dir / "figs" / "example.png").exists()


def test_pdf_embedded_figures_preserved_when_present(tmp_path: Path):
    sample = _make_pdf_with_embedded_figure(tmp_path / "ieee_embedded.pdf")
    result = route_and_run("ieee", "acm", sample, tmp_path / "job4")
    converted_dir = Path(result.converted_source_path)
    converted_main = (converted_dir / "main.tex").read_text(encoding="utf-8")
    assert "\\includegraphics[width=\\linewidth]{figures/" in converted_main
    assert converted_main.count("\\includegraphics[width=\\linewidth]{figures/") == 1
    assert any((converted_dir / "figures").glob("*"))
    assert "\\begin{thebibliography}" in converted_main


def test_pdf_vector_figures_are_cropped_when_no_embedded_image(tmp_path: Path):
    sample = _make_pdf_with_vector_figure(tmp_path / "ieee_vector_figure.pdf")
    result = route_and_run("ieee", "acm", sample, tmp_path / "job_vector")
    converted_dir = Path(result.converted_source_path)
    converted_main = (converted_dir / "main.tex").read_text(encoding="utf-8")
    assert "\\includegraphics[width=\\linewidth]{figures/" in converted_main
    assert "% Figure asset unavailable from PDF ingest" not in converted_main
    assert any((converted_dir / "figures").glob("figure_p*.png"))


def test_acm_pdf_with_unnumbered_sections_routes_to_ieee(tmp_path: Path):
    sample = _make_pdf_with_unnumbered_sections(tmp_path / "acm_unnumbered.pdf")
    result = route_and_run("acm", "ieee", sample, tmp_path / "job5")
    converted_dir = Path(result.converted_source_path)
    converted_main = (converted_dir / "main.tex").read_text(encoding="utf-8")
    assert "\\documentclass[conference]{IEEEtran}" in converted_main
    assert "\\section{Introduction}" in converted_main
    assert "\\section{Literature Review Categories}" in converted_main
    assert "\\begin{thebibliography}" in converted_main
    assert "placeholder for ref1" not in converted_main
