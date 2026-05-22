from __future__ import annotations

import pytest

from paper_conversion_system.openclaw import (
    _assert_includegraphics_preserved,
    _detect_unresolved,
    _extract_includegraphics_paths,
    _strip_latex_comments,
)
from paper_conversion_system.orchestrator import _line_change_ratio


def test_openclaw_leakage_detection_uses_shared_forbidden_patterns() -> None:
    issues = _detect_unresolved(r"\documentclass[sigconf]{acmart}\begin{acks}x\end{acks}", "ieee")
    assert any("acmart" in issue for issue in issues)
    assert any("acks" in issue for issue in issues)


def test_includegraphics_audit_detects_dropped_paths() -> None:
    source_paths = _extract_includegraphics_paths(
        r"\includegraphics[width=\linewidth]{figures/One.PNG}"
        "\n"
        r"\includegraphics{plots/two.pdf}"
    )
    assert source_paths == {"figures/one", "plots/two"}
    with pytest.raises(RuntimeError, match="dropped includegraphics"):
        _assert_includegraphics_preserved(source_paths, r"\includegraphics{figures/one}")


def test_latex_comments_are_stripped_before_llm_prompt() -> None:
    stripped = _strip_latex_comments(
        r"Visible text % adversarial prompt"
        "\n"
        r"Escaped percent \% should remain"
    )
    assert "adversarial prompt" not in stripped
    assert r"Escaped percent \% should remain" in stripped


def test_line_change_ratio_flags_broad_rewrites() -> None:
    before = "\n".join(f"line {idx}" for idx in range(10))
    after = "\n".join([f"line {idx}" for idx in range(2)] + [f"new {idx}" for idx in range(8)])
    assert _line_change_ratio(before, after) > 0.7
