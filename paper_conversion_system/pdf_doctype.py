from __future__ import annotations

import re


def detect_pdf_document_type(text: str) -> tuple[str, dict]:
    sample = text[:20000]
    lower = sample.lower()

    thesis_score = 0
    paper_score = 0
    ieee_score = 0
    acm_score = 0

    thesis_cues = [
        "dissertation",
        "thesis",
        "graduate school",
        "committee members",
        "doctor of philosophy",
        "defense",
        "dedication",
        "table of contents",
        "list of figures",
        "list of tables",
    ]
    for cue in thesis_cues:
        if cue in lower:
            thesis_score += 1

    paper_cues = [
        "abstract",
        "introduction",
        "references",
        "keywords",
        "index terms",
        "doi",
    ]
    for cue in paper_cues:
        if cue in lower:
            paper_score += 1

    if "ieee" in lower:
        ieee_score += 1
    if "index terms" in lower:
        ieee_score += 1
    if "acm" in lower:
        acm_score += 1
    if "acm reference format" in lower:
        acm_score += 1

    if thesis_score >= 3:
        return "thesis_dissertation", {
            "thesis_score": thesis_score,
            "paper_score": paper_score,
            "ieee_score": ieee_score,
            "acm_score": acm_score,
        }
    if ieee_score >= 1:
        return "ieee_paper", {
            "thesis_score": thesis_score,
            "paper_score": paper_score,
            "ieee_score": ieee_score,
            "acm_score": acm_score,
        }
    if acm_score >= 1:
        return "acm_paper", {
            "thesis_score": thesis_score,
            "paper_score": paper_score,
            "ieee_score": ieee_score,
            "acm_score": acm_score,
        }
    return "generic_academic_pdf", {
        "thesis_score": thesis_score,
        "paper_score": paper_score,
        "ieee_score": ieee_score,
        "acm_score": acm_score,
    }


def extract_thesis_body_text(text: str) -> str:
    lower = text.lower()
    start = 0
    patterns = [
        r"chapter\s+1\s+introduction",
        r"1\.1\s+thesis statement",
        r"introduction",
    ]
    for pattern in patterns:
        m = re.search(pattern, lower)
        if m:
            start = m.start()
            break
    trimmed = text[start:]
    return trimmed
