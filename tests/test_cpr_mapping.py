from pathlib import Path
import time

from paper_conversion_system.cpr import parse_project_to_cpr
from paper_conversion_system.models import CanonicalPaperRepresentation, Figure, Reference, Section, Table
from paper_conversion_system.pdf_cleanup import clean_pdf_text
from paper_conversion_system.pdf_ingest import _detect_equation_regions, _extract_frontmatter, _extract_sections
from paper_conversion_system.pdf_postprocess import _add_subsection_markers, _separate_references
from paper_conversion_system.render import _guess_bibtex_fields, _remove_equation_residue, render_cpr_to_target


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


def test_pdf_style_reference_guessing_prefers_author_title_split():
    entry_type, fields = _guess_bibtex_fields(
        "ref4",
        "C. Cotsaces, N. Nikolaidis, and I. Pitas. Face-based digital signatures for video retrieval. IEEE Transactions on Circuits and Systems for Video Technology, 18(4):549-553, 2008.",
    )
    assert entry_type == "article"
    assert fields["author"] == "C. Cotsaces, N. Nikolaidis, and I. Pitas"
    assert fields["title"] == "Face-based digital signatures for video retrieval"
    assert "Transactions on Circuits and Systems for Video Technology" in fields["journal"]


def test_pdf_table_asset_is_reinserted_near_anchor(tmp_path: Path):
    cpr = CanonicalPaperRepresentation(
        title="Anchor Test",
        authors=["Alice"],
        sections=[
            Section(
                title="Experiment",
                content="The validation performance rate was measured carefully. The results were run through a confusion matrix and summarized below.",
            )
        ],
        tables=[
            Table(
                label="tab:iv",
                caption="Performance measurement",
                latex="\\centering\n\\includegraphics[width=\\linewidth]{artifacts/tables/table_p4_1.png}",
                placement="H",
            )
        ],
        metadata={
            "table_section_map": {"tab:iv": "Experiment"},
            "table_anchor_map": {"tab:iv": "The validation performance rate was measured carefully."},
            "ingest_mode": "pdf",
        },
    )
    out = tmp_path / "out"
    render_cpr_to_target(cpr, "acm", out)
    text = (out / "main.tex").read_text(encoding="utf-8")
    assert "\\includegraphics[width=\\linewidth]{artifacts/tables/table_p4_1.png}" in text
    assert text.index("The validation performance rate was measured carefully.") < text.index("\\begin{table}[H]")


def test_pdf_references_render_as_thebibliography_when_requested(tmp_path: Path):
    cpr = CanonicalPaperRepresentation(
        title="Refs",
        authors=["Alice"],
        sections=[Section(title="Introduction", content="See \\cite{ref1}.")],
        references=[Reference(key="ref1", raw="A. Author, Sample paper, 2024.")],
        metadata={"ingest_mode": "pdf", "bibliography_mode": "thebibliography"},
    )
    out = tmp_path / "out"
    render_cpr_to_target(cpr, "acm", out)
    text = (out / "main.tex").read_text(encoding="utf-8")
    assert "\\begin{thebibliography}" in text
    assert "\\bibitem{ref1} A. Author, Sample paper, 2024." in text
    assert "\\bibliography{references}" not in text


def test_frontmatter_escapes_ampersands_for_acm(tmp_path: Path):
    cpr = CanonicalPaperRepresentation(
        title="Ampersand",
        authors=["Alice & Bob"],
        sections=[Section(title="Introduction", content="Hello.")],
        metadata={"affiliations": ["North Carolina A&T State University"]},
    )
    out = tmp_path / "out"
    render_cpr_to_target(cpr, "acm", out)
    text = (out / "main.tex").read_text(encoding="utf-8")
    assert "\\author{Alice \\& Bob}" in text
    assert "\\institution{North Carolina A\\&T State University}" in text


def test_acm_render_uses_structured_author_profiles(tmp_path: Path):
    cpr = CanonicalPaperRepresentation(
        title="Credential Test",
        authors=["Ali Abdullah S. AlQahtani", "Hosam Alamleh", "Zakaria El-Awadi"],
        abstract="Abstract",
        sections=[Section(title="Introduction", content="Body text.")],
        metadata={
            "author_profiles": [
                {
                    "name": "Ali Abdullah S. AlQahtani",
                    "department": "Computer Systems Technology",
                    "institution": "North Carolina A&T State University",
                    "city": "Greensboro",
                    "state": "North Carolina",
                    "country": "USA",
                    "email": "AlQahtani.aasa@gmail.com",
                },
                {
                    "name": "Hosam Alamleh",
                    "department": "Computer science",
                    "institution": "University of North Carolina Wilmington",
                    "city": "Wilmington",
                    "state": "NC",
                    "email": "hosam.amleh@gmail.com",
                },
                {
                    "name": "Zakaria El-Awadi",
                    "department": "Cyberspace Engineering",
                    "institution": "Louisiana Tech University",
                    "city": "Ruston",
                    "state": "Louisiana",
                    "country": "USA",
                    "email": "Znelawadi@gmail.com",
                },
            ],
            "ingest_mode": "pdf",
        },
    )
    out = tmp_path / "out"
    render_cpr_to_target(cpr, "acm", out)
    text = (out / "main.tex").read_text(encoding="utf-8")
    assert "\\author{Greensboro}" not in text
    assert "\\author{USA}" not in text
    assert "\\department{Computer Systems Technology}" in text
    assert "\\institution{North Carolina A\\&T State University}" in text
    assert "\\city{Greensboro}" in text
    assert "\\country{USA}" in text


def test_pdf_artifact_text_is_removed_when_visual_fallback_is_inserted(tmp_path: Path):
    cpr = CanonicalPaperRepresentation(
        title="Cleanup",
        authors=["Alice"],
        sections=[
            Section(
                title="Experiment",
                content=(
                    "The results are summarized below. "
                    "TABLE III MAXIMUM OF 60 RSSI VALUES RESULTS "
                    "Actual points Estimated points Difference "
                    "(2,17) (4.2,14.3) 3.6 ft "
                    "Equation block x = y + z (1)"
                ),
            )
        ],
        tables=[
            Table(
                label="tab:iii",
                caption="MAXIMUM OF 60 RSSI VALUES RESULTS",
                latex="\\centering\n\\includegraphics[width=\\linewidth]{artifacts/tables/table_p4_1.png}",
                placement="H",
            )
        ],
        metadata={
            "ingest_mode": "pdf",
            "table_section_map": {"tab:iii": "Experiment"},
            "table_anchor_map": {"tab:iii": "The results are summarized below."},
            "table_text_map": {
                "tab:iii": (
                    "TABLE III MAXIMUM OF 60 RSSI VALUES RESULTS "
                    "Actual points Estimated points Difference "
                    "(2,17) (4.2,14.3) 3.6 ft"
                )
            },
            "equation_artifacts": [
                {
                    "label": "eqimg:1:1",
                    "path": "artifacts/equations/equation_p4_1.png",
                    "anchor_text": "The results are summarized below.",
                    "section_title": "Experiment",
                    "raw_text": "Equation block x = y + z (1)",
                }
            ],
        },
    )
    out = tmp_path / "out"
    render_cpr_to_target(cpr, "acm", out)
    text = (out / "main.tex").read_text(encoding="utf-8")
    assert "TABLE III MAXIMUM OF 60 RSSI VALUES RESULTS Actual points Estimated points Difference" not in text
    assert "Equation block x = y + z (1)" not in text
    assert "\\includegraphics[width=\\linewidth]{artifacts/tables/table_p4_1.png}" in text
    assert "\\includegraphics[width=0.72\\linewidth]{artifacts/equations/equation_p4_1.png}" in text


def test_pdf_section_heading_keeps_dropcap_in_body():
    text = (
        "I. INTRODUCTION\n"
        "D\n"
        "IGITAL signatures are based on public-key cryptography and explain the method clearly.\n"
        "II. CONCLUSION\n"
        "Closing remarks go here with enough text to stay above the fallback threshold."
    )
    sections = _extract_sections(text)
    assert sections[0].title == "Introduction"
    assert sections[0].content.startswith("DIGITAL signatures")


def test_pdf_extract_sections_detects_unnumbered_headings():
    text = (
        "Secure Digital Signature Validated by Ambient Users\n"
        "Abstract\n"
        "This abstract introduces the paper and contains enough text to be skipped by section detection.\n"
        "Keywords\n"
        "digital signature, wifi, validation\n"
        "Introduction\n"
        "This section explains the system in enough detail to clear the fallback threshold and remain as body text.\n"
        "Literature Review Categories\n"
        "This section surveys prior work and adds enough prose to qualify as a real extracted section.\n"
        "REFERENCES\n"
        "[1] Demo reference entry.\n"
    )
    sections = _extract_sections(text)
    assert [section.title for section in sections] == [
        "Introduction",
        "Literature Review Categories",
        "References",
    ]
    assert sections[0].content.startswith("This section explains the system")


def test_pdf_extract_sections_detects_early_introduction_after_short_frontmatter():
    text = (
        "Sample Paper\n"
        "Abstract\n"
        "Short abstract text.\n"
        "Introduction\n"
        "This is enough body text to clear the threshold and should be recognized as the first section.\n"
        "Methods\n"
        "This is another section with enough body text to clear the threshold too.\n"
    )
    sections = _extract_sections(text)
    assert [section.title for section in sections] == ["Introduction", "Methods"]


def test_clean_pdf_text_preserves_first_repeated_title_line():
    raw = (
        "Secure Digital Signature Validated by Ambient\n"
        "User's Wi-Fi-enabled devices\n"
        "Abstract\n"
        "Short abstract text.\n"
        "ACM Reference Format:\n"
        "Secure Digital Signature Validated by Ambient\n"
        "User's Wi-Fi-enabled devices\n"
    )
    cleaned, _ = clean_pdf_text(raw, mode="safe")
    assert cleaned.count("Secure Digital Signature Validated by Ambient") == 1
    assert "User's Wi-Fi-enabled devices" in cleaned


def test_pdf_extract_frontmatter_prefers_title_and_filters_locations():
    lines = [
        "Secure Digital Signature Validated by Ambient",
        "User's Wi-Fi-enabled devices",
        "Ali Abdullah S. AlQahtani",
        "Computer Systems Technology",
        "North Carolina A&T State University",
        "Greensboro, North Carolina, USA",
        "AlQahtani.aasa@gmail.com",
        "Hosam Alamleh",
        "Computer science",
        "University of North Carolina Wilmington",
        "Wilmington, NC",
        "hosam.amleh@gmail.com",
        "Zakaria El-Awadi",
        "Cyberspace Engineering",
        "Louisiana Tech University",
        "Ruston, Louisiana, USA",
        "Znelawadi@gmail.com",
        "Abstract",
    ]
    title, authors, metadata = _extract_frontmatter(lines)
    assert title == "Secure Digital Signature Validated by Ambient User's Wi-Fi-enabled devices"
    assert authors == ["Ali Abdullah S. AlQahtani", "Hosam Alamleh", "Zakaria El-Awadi"]
    assert metadata["emails"] == [
        "AlQahtani.aasa@gmail.com",
        "hosam.amleh@gmail.com",
        "Znelawadi@gmail.com",
    ]
    assert "North Carolina" not in authors
    assert "Computer Systems Technology" not in authors
    assert metadata["author_profiles"][0] == {
        "name": "Ali Abdullah S. AlQahtani",
        "department": "Computer Systems Technology",
        "institution": "North Carolina A&T State University",
        "city": "Greensboro",
        "state": "North Carolina",
        "country": "USA",
        "email": "AlQahtani.aasa@gmail.com",
    }
    assert metadata["author_profiles"][1]["city"] == "Wilmington"
    assert metadata["author_profiles"][1]["state"] == "NC"
    assert metadata["author_profiles"][2]["country"] == "USA"


def test_pdf_extract_sections_preserves_paragraph_breaks():
    text = (
        "Introduction\n"
        "This is the first paragraph of the section and it keeps running\n"
        "onto the next extracted line for layout reasons.\n"
        "\n"
        "This is the second paragraph and it should stay separate in the CPR.\n"
        "References\n"
        "[1] Demo reference entry.\n"
    )
    sections = _extract_sections(text)
    assert sections[0].title == "Introduction"
    assert "\n\n" in sections[0].content
    assert sections[0].content.startswith("This is the first paragraph")


def test_pdf_subsection_markers_stop_before_following_body_prose():
    cpr = CanonicalPaperRepresentation(
        title="Subsections",
        authors=["Alice"],
        sections=[
            Section(
                title="Experiment",
                content=(
                    "A. Position accuracy In this experiment, values are compared carefully. "
                    "B. Performance Measurement The validation success rate is reported below."
                ),
            )
        ],
    )
    updated = _add_subsection_markers(cpr)
    content = updated.sections[0].content
    assert "\\subsection{Position accuracy} In this experiment" in content
    assert "\\subsection{Performance Measurement} The validation success rate" in content
    assert "\\subsection{Position accuracy In this" not in content


def test_pdf_embedded_references_are_split_from_body_section():
    cpr = CanonicalPaperRepresentation(
        title="Embedded references",
        authors=["Alice"],
        sections=[
            Section(
                title="Body",
                content=(
                    "Introduction text continues through the final discussion with enough detail to stay in the body section. "
                    "REFERENCES [1] A. Author, Sample paper, 2024. [2] B. Author, Another paper, 2023."
                ),
            )
        ],
        references=[Reference(key="ref1", raw="placeholder for ref1"), Reference(key="ref2", raw="placeholder for ref2")],
    )
    updated = _separate_references(cpr)
    assert [section.title for section in updated.sections] == ["Body", "References"]
    assert "REFERENCES" not in updated.sections[0].content
    assert updated.references[0].raw == "A. Author, Sample paper, 2024."
    assert updated.references[1].raw == "B. Author, Another paper, 2023."


def test_pdf_body_prose_does_not_false_split_on_lowercase_references_word():
    cpr = CanonicalPaperRepresentation(
        title="Body references",
        authors=["Alice"],
        sections=[
            Section(
                title="Body",
                content=(
                    "This discussion references [1] and [2] in passing before continuing with more explanation about the method and results."
                ),
            )
        ],
        references=[],
    )
    updated = _separate_references(cpr)
    assert [section.title for section in updated.sections] == ["Body"]
    assert updated.references == []


def test_equation_region_groups_multiline_system_without_table_rows():
    lines = [
        {"text": "3) The previous step outcome will be run through equation (2) to estimate the user's device's position", "bbox": (75.2, 416.4, 297.3, 426.4)},
        {"text": "(i.e., a user's x and y coordinates).", "bbox": (89.4, 440.1, 233.1, 450.3)},
        {"text": "(x −x1)2 + (y −y1)2 = d2", "bbox": (136.2, 464.7, 250.1, 479.6)},
        {"text": "1", "bbox": (246.1, 473.6, 250.1, 480.6)},
        {"text": "(x −x2)2 + (y −y2)2 = d2", "bbox": (136.2, 479.9, 250.1, 494.8)},
        {"text": "Actual points", "bbox": (344.1, 483.0, 388.7, 491.0)},
        {"text": "(2)", "bbox": (285.7, 484.2, 297.3, 494.2)},
        {"text": "2", "bbox": (246.1, 488.8, 250.1, 495.8)},
        {"text": "(x −x3)2 + (y −y3)2 = d2", "bbox": (136.2, 495.1, 250.1, 510.0)},
        {"text": "3", "bbox": (246.1, 504.0, 250.1, 510.9)},
        {"text": "4) After finding the user's Wi-Fi enabled devices x and y coordinates", "bbox": (75.2, 534.8, 297.3, 545.0)},
    ]
    regions = _detect_equation_regions(lines, 595.0)
    assert len(regions) == 1
    assert "Actual points" not in regions[0]["raw_text"]
    assert "(x −x3)2 + (y −y3)2 = d2" in regions[0]["raw_text"]


def test_pdf_figure_is_attached_to_matching_section_without_explicit_map(tmp_path: Path):
    cpr = CanonicalPaperRepresentation(
        title="Figure placement",
        authors=["Alice"],
        sections=[
            Section(
                title="Method",
                content="The workflow is summarized in Figure 1 before we move into the evaluation details.",
            ),
            Section(
                title="Evaluation",
                content="The experiment reports metrics and outcomes without referencing the overview figure directly.",
            ),
        ],
        figures=[
            Figure(
                label="fig:1",
                caption="Workflow overview for ambient validation pipeline",
                path="figures/figure_p1_1.png",
            )
        ],
        metadata={"ingest_mode": "pdf"},
    )
    out = tmp_path / "out"
    render_cpr_to_target(cpr, "ieee", out)
    text = (out / "main.tex").read_text(encoding="utf-8")
    assert text.index("\\section{Method}") < text.index("\\begin{figure}[tbp]")
    assert text.index("\\begin{figure}[tbp]") < text.index("\\section{Evaluation}")


def test_pdf_render_preserves_paragraph_breaks_for_acm(tmp_path: Path):
    cpr = CanonicalPaperRepresentation(
        title="Paragraphs",
        authors=["Alice"],
        sections=[
            Section(
                title="Introduction",
                content=(
                    "This is the first paragraph of the converted paper.\n\n"
                    "This is the second paragraph of the converted paper."
                ),
            )
        ],
        metadata={"ingest_mode": "pdf"},
    )
    out = tmp_path / "out"
    render_cpr_to_target(cpr, "acm", out)
    text = (out / "main.tex").read_text(encoding="utf-8")
    assert "This is the first paragraph of the converted paper.\n\nThis is the second paragraph of the converted paper." in text


def test_acm_render_adds_conference_stub_frontmatter(tmp_path: Path):
    cpr = CanonicalPaperRepresentation(
        title="Conference Stub",
        authors=["Alice"],
        sections=[Section(title="Introduction", content="Hello world.")],
    )
    out = tmp_path / "out"
    render_cpr_to_target(cpr, "acm", out)
    text = (out / "main.tex").read_text(encoding="utf-8")
    assert "\\setcopyright{none}" in text
    assert "\\settopmatter{printacmref=false}" in text
    assert "\\acmConference[Converted Paper]{Converted Paper}{}{}" in text


def test_equation_region_variants_include_assignment_form():
    lines = [
        {"text": "(x2 −x1)2 + (y2 −y1)2", "bbox": (169.4, 587.9, 268.9, 602.1)},
        {"text": "p", "bbox": (159.4, 589.0, 169.4, 598.9)},
        {"text": "Distance =", "bbox": (105.7, 590.8, 156.7, 600.8)},
        {"text": "(3)", "bbox": (285.7, 591.0, 297.3, 601.0)},
    ]
    regions = _detect_equation_regions(lines, 595.0)
    assert len(regions) == 1
    assert "Distance = p (x2 −x1)2 + (y2 −y1)2 (3)" in regions[0]["scrub_variants"]
