from __future__ import annotations

from pathlib import Path

from paper_conversion_system.agents import (
    April,
    Friday,
    _parse_ieee_author_profiles,
    _split_names,
    _strip_acm_ceremony_macros,
)


def test_april_translates_ieee_author_blocks_to_acm_profiles(tmp_path: Path) -> None:
    src = tmp_path / "ieee_src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "references.bib").write_text(
        "@inproceedings{x, author={A}, title={T}, booktitle={B}, year={2020}}",
        encoding="utf-8",
    )
    (src / "main.tex").write_text(
        r"""
\documentclass[conference]{IEEEtran}
\title{Test}
\author{
\IEEEauthorblockN{Alice Smith, Bob Jones}
\IEEEauthorblockA{\textit{School of Computing}\\
Example University\\
alice@example.edu, bob@example.edu}
}
\begin{document}
\maketitle
\begin{abstract}x\end{abstract}
\section{Intro}x
\bibliographystyle{IEEEtran}
\bibliography{references}
\end{document}
""",
        encoding="utf-8",
    )

    out = tmp_path / "acm_out"
    out.mkdir(parents=True, exist_ok=True)
    _, report, cpr = April().convert(src, out)

    assert cpr.metadata.get("author_profiles"), "April should populate author_profiles"
    assert any("translated IEEEauthorblockN/A" in w for w in report.warnings)
    main_tex = (out / "main.tex").read_text(encoding="utf-8")
    assert r"\author{Alice Smith}" in main_tex
    assert r"\author{Bob Jones}" in main_tex
    assert r"\affiliation{" in main_tex
    assert "alice@example.edu" in main_tex


def test_friday_strips_acm_ceremony_and_emits_ieee_header(tmp_path: Path) -> None:
    src = tmp_path / "acm_src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "references.bib").write_text(
        "@article{x, author={A}, title={T}, journal={J}, year={2020}}",
        encoding="utf-8",
    )
    (src / "main.tex").write_text(
        r"""
\documentclass[sigconf]{acmart}
\setcopyright{acmlicensed}
\acmConference{DemoConf}{2026}{Austin, TX}
\acmDOI{10.1145/123}
\title{Test}
\author{Alice Smith}
\affiliation{\institution{Example University}\country{USA}}
\email{alice@example.edu}
\begin{document}
\begin{abstract}x\end{abstract}
\maketitle
\section{Intro}x
\bibliographystyle{ACM-Reference-Format}
\bibliography{references}
\end{document}
""",
        encoding="utf-8",
    )

    out = tmp_path / "ieee_out"
    out.mkdir(parents=True, exist_ok=True)
    _, report, _ = Friday().convert(src, out)
    main_tex = (out / "main.tex").read_text(encoding="utf-8")

    assert r"\IEEEoverridecommandlockouts" in main_tex
    assert r"\setcopyright{" not in main_tex
    assert r"\acmConference{" not in main_tex
    assert r"\acmDOI{" not in main_tex
    assert any("stripped ACM ceremony macros" in w for w in report.warnings)


def test_parse_ieee_author_profiles_handles_nested_textit_and_href() -> None:
    """Affiliation blocks with nested commands previously truncated at the first }."""
    latex = (
        r"\author{"
        r"\IEEEauthorblockN{Alice Smith}"
        r"\IEEEauthorblockA{"
        r"\textit{School of Computing}\\"
        r"\textit{Example University}\\"
        r"Bristol, UK\\"
        r"\href{mailto:alice@ex.edu}{alice@ex.edu}"
        r"}"
        r"}"
    )
    profiles = _parse_ieee_author_profiles(latex)
    assert len(profiles) == 1
    p = profiles[0]
    assert p["name"] == "Alice Smith"
    assert "School of Computing" in p["institution"]
    assert "Example University" in p["institution"]
    assert p["email"] == "alice@ex.edu"


def test_parse_ieee_author_profiles_emits_one_profile_per_name_in_group() -> None:
    """Multiple names in a single block should each become a profile."""
    latex = (
        r"\IEEEauthorblockN{Alice Smith \and Bob Jones}"
        r"\IEEEauthorblockA{Acme University\\alice@a.com, bob@a.com}"
    )
    profiles = _parse_ieee_author_profiles(latex)
    names = [p["name"] for p in profiles]
    assert names == ["Alice Smith", "Bob Jones"]
    assert profiles[0]["email"] == "alice@a.com"
    assert profiles[1]["email"] == "bob@a.com"


def test_strip_acm_ceremony_handles_nested_brace_arguments() -> None:
    """ACM ceremony stripper must not bail when arguments contain nested groups."""
    text = (
        r"\setcopyright{acmlicensed}"
        "\n"
        r"\acmConference[Foo'25]{The Foo \& Bar Conference}{2025}{New York, NY}"
        "\n"
        r"\acmDOI{\href{https://doi.org/10.1145/x}{10.1145/x}}"
        "\n"
        r"\acmISBN{978-1-2345}"
        "\n"
        r"\title{Keep me}"
    )
    cleaned, removed = _strip_acm_ceremony_macros(text)
    assert "setcopyright" in removed
    assert "acmConference" in removed
    assert "acmDOI" in removed
    assert "acmISBN" in removed
    assert r"\setcopyright" not in cleaned
    assert r"\acmConference" not in cleaned
    assert r"\acmDOI" not in cleaned
    # Must not eat the next command.
    assert r"\title{Keep me}" in cleaned


def test_strip_acm_ceremony_does_not_strip_unrelated_acm_macros() -> None:
    """Token-boundary check prevents matching command prefixes."""
    text = r"\acmDOIfake{not-really-a-doi}\acmDOI{real-doi}"
    cleaned, removed = _strip_acm_ceremony_macros(text)
    assert "acmDOI" in removed
    assert r"\acmDOIfake{not-really-a-doi}" in cleaned
    assert r"\acmDOI{real-doi}" not in cleaned


def test_friday_strips_multiline_acm_metadata_from_preserved_preamble(tmp_path: Path) -> None:
    """Multi-line ACM metadata in source preamble should not leak into IEEE output."""
    src = tmp_path / "acm_src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "references.bib").write_text(
        "@article{x, author={A}, title={T}, journal={J}, year={2020}}",
        encoding="utf-8",
    )
    (src / "main.tex").write_text(
        r"""
\documentclass[sigconf]{acmart}
\setcopyright{acmlicensed}
\acmConference[Foo'25]{The Foo \& Bar
  International Conference}{2025}{New
  York, NY}
\acmDOI{\href{https://doi.org/10.1145/x}{10.1145/x}}
\acmJournal{TOIS}
\acmVolume{42}
\acmNumber{3}
\authornote{First-author note that\\
  spans multiple lines\\
  with brace nesting \{ok\}}
\title{Multi-line\\Title}
\author{Alice Smith}
\affiliation{\institution{Example University}\country{USA}}
\email{alice@example.edu}
\begin{document}
\begin{abstract}x\end{abstract}
\maketitle
\section{Intro}x
\bibliographystyle{ACM-Reference-Format}
\bibliography{references}
\end{document}
""",
        encoding="utf-8",
    )

    out = tmp_path / "ieee_out"
    out.mkdir(parents=True, exist_ok=True)
    Friday().convert(src, out)
    main_tex = (out / "main.tex").read_text(encoding="utf-8")

    # Every ACM ceremony command should be gone, including the multi-line ones.
    for forbidden in (
        r"\setcopyright",
        r"\acmConference",
        r"\acmDOI",
        r"\acmJournal",
        r"\acmVolume",
        r"\acmNumber",
        r"\authornote",
    ):
        assert forbidden not in main_tex, f"{forbidden} leaked into IEEE output"
    # Multi-line title body must not leave a dangling "York, NY" or "International Conference" line.
    assert "International Conference" not in main_tex
    assert "York, NY" not in main_tex


def test_split_names_handles_realistic_ieee_separators() -> None:
    # \and with word boundary, \\, \quad, and , are all valid separators.
    cases = [
        (r"Alice Smith \and Bob Jones", ["Alice Smith", "Bob Jones"]),
        (r"Alice Smith, Bob Jones; Carol Davis", ["Alice Smith", "Bob Jones", "Carol Davis"]),
        (r"Alice Smith \\ Bob Jones", ["Alice Smith", "Bob Jones"]),
        (r"Alice Smith \quad Bob Jones \qquad Carol Davis",
         ["Alice Smith", "Bob Jones", "Carol Davis"]),
    ]
    for raw, expected in cases:
        assert _split_names(raw) == expected, raw


def test_split_names_does_not_match_command_prefix() -> None:
    # \andersen should not be split on \and — the word-boundary lookahead
    # protects against false positives.
    assert _split_names(r"\Andersen \and Bob Jones") == [r"\Andersen", "Bob Jones"]


def test_strip_acm_ceremony_handles_journal_metadata() -> None:
    """Journal-mode acmart docs use a different metadata family."""
    text = (
        r"\acmJournal{TOIS}"
        "\n"
        r"\acmVolume{42}"
        "\n"
        r"\acmNumber{3}"
        "\n"
        r"\acmArticle{17}"
        "\n"
        r"\acmMonth{6}"
        "\n"
    )
    cleaned, removed = _strip_acm_ceremony_macros(text)
    for label in ("acmJournal", "acmVolume", "acmNumber", "acmArticle", "acmMonth"):
        assert label in removed
    assert cleaned.strip() == ""
