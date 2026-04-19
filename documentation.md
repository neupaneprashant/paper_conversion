# Paper Conversion System Documentation

## Overview

This project is a multi-stage academic paper conversion system designed to convert between IEEE and ACM formats using agent roles, a canonical intermediate representation, structured validation, compile orchestration, and an experimental PDF ingestion pipeline.

Primary agent roles:
- **April** — IEEE to ACM conversion specialist
- **Friday** — ACM to IEEE conversion specialist
- **Comp** — orchestration, harmonization, validation, compile, and artifact packaging

The system supports two major source modes:
1. **LaTeX project ingestion** — the strongest and preferred path
2. **PDF ingestion** — an experimental recovery path that attempts to extract structure and build a CPR before conversion

---

## Architecture

### High-level pipeline

```text
Input
├─ LaTeX project
│  └─ Parse to CPR
└─ PDF
   ├─ Document-type detection
   ├─ PDF cleanup
   ├─ PDF ingest / thesis-aware alternate parser
   └─ Refine to CPR

CPR
├─ April (IEEE -> ACM)
├─ Friday (ACM -> IEEE)
└─ Comp
   ├─ harmonization
   ├─ validation
   ├─ compile orchestration
   └─ packaging
```

### Core ideas

- **Never convert directly between templates if avoidable.** Use CPR as an intermediate format.
- **Preserve semantics over syntax.** Claims, equations, references, and section meaning should survive conversion.
- **Treat PDF as recovery, not truth.** PDF conversion is inherently noisier than source-LaTeX conversion.
- **Emit warnings rather than fabricate missing content.**

---

## Main modules

### Core conversion
- `paper_conversion_system/agents.py`
  - April and Friday conversion agents
- `paper_conversion_system/orchestrator.py`
  - routing and Comp orchestration
- `paper_conversion_system/render.py`
  - renders CPR into target LaTeX output
- `paper_conversion_system/templates.py`
  - ACM and IEEE template skeletons
- `paper_conversion_system/normalization.py`
  - CPR cleanup before target rendering
- `paper_conversion_system/validators.py`
  - template and structural validation
- `paper_conversion_system/compiler.py`
  - compile attempts and repair logic

### CPR and parsing
- `paper_conversion_system/models.py`
  - CPR data structures and reports
- `paper_conversion_system/cpr.py`
  - LaTeX project to CPR parser
- `paper_conversion_system/contracts.py`
  - input/output contracts
- `paper_conversion_system/rules.py`
  - target template rules and profiles

### PDF pipeline
- `paper_conversion_system/pdf_cleanup.py`
  - safe and aggressive PDF cleanup passes
- `paper_conversion_system/pdf_doctype.py`
  - paper vs thesis/dissertation detection
- `paper_conversion_system/pdf_ingest.py`
  - main PDF to CPR path
- `paper_conversion_system/pdf_postprocess.py`
  - figure/table/reference/frontmatter refinement after PDF CPR extraction
- `paper_conversion_system/pdf_thesis.py`
  - alternate thesis/dissertation parser path
- `paper_conversion_system/pdf_parser.py`
  - legacy or alternate PDF conversion utility path
- `paper_conversion_system/structure_analyzer.py`
  - structure-level PDF analysis helpers
- `paper_conversion_system/latex_generator.py`
  - direct PDF-to-LaTeX generation helpers

### App / API / UI
- `paper_conversion_system/api.py`
  - local HTTP API
- `paper_conversion_system/server.py`
  - server runner
- `paper_conversion_system/job_store.py`
  - job metadata and file layout
- `paper_conversion_system/packaging.py`
  - zip packaging for artifacts
- `ui/`
  - frontend pages and scripts

---

## Canonical Paper Representation (CPR)

CPR is the central internal schema that lets the system normalize different input sources and render them to different targets.

### Main CPR fields
- `title`
- `authors`
- `abstract`
- `keywords`
- `sections`
- `figures`
- `tables`
- `equations`
- `references`
- `metadata`

### Why CPR matters
Without CPR, every conversion would require brittle direct template-to-template rewriting. CPR provides:
- a uniform semantic representation
- a place for cleanup/normalization
- a stable handoff between ingestion and rendering

---

## LaTeX ingestion path

### Flow
1. Detect main `.tex` file
2. Extract title, author block, abstract, keywords, sections, references
3. Create CPR
4. Normalize CPR for target format
5. Render to ACM or IEEE target template
6. Validate, compile, and package

### Strengths
- best fidelity
- best preservation of structure
- easiest bibliography handling
- closest to publication-quality conversion

### Limits
- still heuristic for rich frontmatter mapping
- author/affiliation mapping can be improved further

---

## PDF ingestion path

### Why it exists
Many real users only have a PDF. PDF ingestion attempts best-effort structure recovery and then uses CPR like the LaTeX path.

### Current PDF flow
1. Extract raw text with PyMuPDF (`fitz`)
2. Detect document type
   - IEEE paper
   - ACM paper
   - thesis/dissertation
   - generic academic PDF
3. Apply cleanup pass
   - safe by default
   - aggressive only after confidence checks where applicable
4. Convert cleaned text into CPR
5. Run PDF-specific postprocessing
   - references
   - frontmatter
   - captions
   - subsections
6. Hand CPR to April or Friday
7. Comp validates and packages output

### PDF cleanup stages
#### Safe pass
Conservative cleanup meant to preserve structure:
- repeated header/footer removal
- IEEE/ACM boilerplate stripping
- spacing normalization
- hyphenation repair
- symbol normalization

#### Aggressive pass
Optional and gated:
- stronger figure/table stripping
- more inline artifact cleanup
- only used when confidence suggests it is safe

### PDF limitations
- text is flattened
- figures/tables are inferred, not truly reconstructed
- author/affiliation pairing is heuristic
- bibliography structure is less reliable than from source LaTeX

---

## Thesis/dissertation alternate path (Option B)

### Why it was added
A second test document showed that treating a dissertation like a conference paper caused severe misparsing.

### Thesis path goals
- detect thesis/dissertation reliably
- avoid polluting paper CPR with committee/frontmatter/TOC junk
- extract title, author, abstract, chapter-style structure, and references more safely

### Current behavior
The thesis path now:
- detects thesis/dissertation PDFs
- extracts a much better title
- extracts a more plausible author
- extracts a meaningful abstract
- attempts chapter-oriented section parsing
- attempts references parsing from the last REFERENCES/BIBLIOGRAPHY occurrence

### Current limitations
Still under active refinement, but materially improved in the 2026-04-19 pass:
- chapter segmentation is now TOC-aware (the parser deliberately avoids the
  TOC's `CHAPTER 1 INTRODUCTION` entry when locating the real chapter 1)
- dotted-leader TOC lines (`Introduction .......... 3`) are stripped before
  body extraction
- TOC / list-of-figures / list-of-tables / committee / dedication blocks are
  removed up to the first chapter marker
- appendix suppression is still regex-based and can miss non-standard layouts
- bibliography parsing is better but still not publication-grade

---

## Conversion agents

### April
IEEE to ACM conversion specialist.

Responsibilities:
- normalize IEEE source into CPR
- map CPR into ACM `acmart`-style structure
- preserve title/authors/abstract/keywords/sections/references
- carry acknowledgments and ACM metadata where possible
- produce conversion report and warnings

### Friday
ACM to IEEE conversion specialist.

Responsibilities:
- normalize ACM source into CPR
- map CPR into IEEEtran-style structure
- convert ACM keywords to IEEE keywords
- warn when ACM-only metadata has no clear IEEE analog
- produce conversion report and warnings

### Comp
Final quality gate.

Responsibilities:
- harmonize target output
- validate structure and target-template expectations
- orchestrate compile passes
- capture logs and artifacts
- package reports and final outputs

---

## Validation and compile logic

### Validation checks
Current checks (expanded 2026-04-19) include:
- **L1 skeleton sanity**: exactly one `\begin{document}` / `\end{document}`,
  no duplicate `\title{...}` declarations, no dangling commands at EOF
- **L2 template compliance**:
  - required-section presence (warn on `Introduction` missing)
  - expected-section presence (warn on `Conclusion` missing)
  - forbidden-source-venue leakage per target:
    - IEEE target must not contain `\ccsdesc`, `\begin{CCSXML}`, `\begin{acks}`,
      `\documentclass[...]{acmart}`, or `\acmConference`
    - ACM target must not contain `\begin{IEEEkeywords}`,
      `\documentclass[...]{IEEEtran}`, or `\IEEEPARstart`
  - documentclass sanity (ACM target must mention `acmart`; IEEE target must
    mention `IEEEtran`)
- **L2 citation compliance**:
  - warn when `\bibliography{...}` / `\printbibliography` is missing
  - warn when `\bibliographystyle` is missing
  - warn when no `\cite{...}` calls are detected
  - warn when `\cite` keys don't match CPR reference keys
- **L3 cross-reference integrity**: unresolved `\ref{fig:...}` / `\ref{tab:...}`
- Broken-command heuristic at EOF

### Compile behavior
Comp attempts compile when toolchain is available.
Current behavior includes:
- ordered compile cycle (pdflatex, bibtex, pdflatex, pdflatex)
- bibliography pass when possible
- limited repair attempts (graphicx injection, `\bibliography` / `\bibliographystyle` insertion, deduplicated `\end{document}`)
- structured failure reporting
- **MiKTeX compatibility (2026-04-19)**: treat non-zero pdflatex returns as
  success when `main.pdf` was produced and the transcript contains
  `Output written on main.pdf`. This prevents MiKTeX's "check for updates"
  warning from being reported as a compile failure.

### LaTeX-special character handling
When CPR originates from PDF ingest (`metadata["ingest_mode"]` starts with
`"pdf"`), body prose is escaped for the LaTeX special characters `& % $ # _ ~ ^`
before being injected into the target template. This stops raw PDF tokens like
`$4.24 million` or `98.87%` from triggering math mode or comment parsing. The
escaper intentionally leaves `\`, `{`, and `}` untouched so that CPR refinement
steps like `\cite{refN}` and `\subsection{...}` survive unchanged.

### Environment caveat
PDF or LaTeX conversion can logically succeed while compile still fails if:
- `pdflatex` is not installed
- `bibtex` is unavailable
- required MiKTeX packages are missing

---

## API/UI overview

### API routes
Implemented local HTTP API includes:
- `GET /api/jobs`
- `POST /api/jobs`
- `GET /api/jobs/:job_id`
- artifact download endpoints
- zip bundle download endpoint

### UI
The UI includes:
- home/convert page
- local input path submission
- direction selection
- job list/status view
- job detail page
- timeline/status presentation for April, Friday, and Comp

---

## Build outputs and directories

Common output directories under `build/` or `jobs/` may contain:
- `converted/`
- `final/`
- `artifacts/`
- `reports/`
- `logs/`
- `job_output.json`

Examples currently present include smoke-test and PDF conversion runs.

---

## Current maturity assessment

### Strongest path
- LaTeX source -> CPR -> April/Friday -> Comp

### Experimental but increasingly capable path
- PDF -> cleanup -> doctype detection -> CPR -> postprocess -> April/Friday -> Comp

### Thesis/dissertation support
- started and partially useful
- not production-ready yet

---

## Known limitations

### General
- no fully native semantic understanding of arbitrary PDF layout
- compile depends on local LaTeX toolchain
- some bibliography reconstruction is heuristic
- some author/affiliation pairing is heuristic

### PDF-specific
- body text is flattened
- figure/table detection can still overfire or miss
- subsection extraction is imperfect
- references parsing is not fully robust across document types

### Thesis-specific
- chapter segmentation still needs improvement
- TOC/list contamination still needs cleanup
- appendix handling is incomplete

---

## Recommended review order

If you are reviewing today, this is the best reading order:

1. `documentation.md`
2. `paper_conversion_system/README.md`
3. `paper_conversion_system/SPEC.md`
4. `paper_conversion_system/PDF_PIPELINE.md`
5. `paper_conversion_system/models.py`
6. `paper_conversion_system/cpr.py`
7. `paper_conversion_system/agents.py`
8. `paper_conversion_system/orchestrator.py`
9. `paper_conversion_system/render.py`
10. `paper_conversion_system/pdf_ingest.py`
11. `paper_conversion_system/pdf_postprocess.py`
12. `paper_conversion_system/pdf_thesis.py`
13. `paper_conversion_system/validators.py`
14. `paper_conversion_system/compiler.py`
15. `paper_conversion_system/api.py`
16. `ui/`

---

## Recommended next engineering steps

### Highest value
1. stabilize thesis chapter segmentation
2. improve TOC/body contamination cleanup
3. improve PDF reference parsing robustness
4. improve author-affiliation pairing fidelity

### Next quality steps
5. better subsection extraction from PDFs
6. reduce figure/table false positives further
7. richer BibTeX field extraction
8. more real-paper regression tests

### Reliability/ops
9. finish local compile toolchain stability
10. add clean verification scripts and commit checkpoints

---

## Summary

This project is now a serious prototype for academic paper conversion with both source-LaTeX and PDF ingestion capabilities. Its strongest path is still source-LaTeX conversion, but the PDF pipeline has advanced significantly beyond simple text extraction and now includes cleanup, document-type detection, CPR construction, postprocessing, and thesis-aware alternate parsing.

The system is already useful for experimentation and structured conversion work, but it still requires further refinement before PDF conversion can be called fully reliable or publication-clean across diverse document types.

---

## Regression status (2026-04-19)

All six unit tests (`tests/test_cpr_mapping.py`, `tests/test_integration.py`,
`tests/test_regressions.py`) pass via the in-repo `run_regressions.py` runner.

End-to-end regressions (see `build/regressions/summary.json` for full JSON):

| Regression | Direction | Template | Citation | Compile | PDF produced |
|---|---|---|---|---|---|
| `samples/ieee_sample` | IEEE -> ACM | pass | pass | success | yes |
| `samples/acm_sample` | ACM -> IEEE | pass | pass | success | yes |
| `ui/Real-Time Prediction ... Hybrid Analog C.pdf` | PDF (thesis) -> ACM | pass | warn | success | yes |

The thesis-PDF regression exercises:
- document-type detection (classified as `thesis_dissertation`)
- thesis-aware title + author + abstract extraction
- TOC stripping and "chapter 1" disambiguation from its TOC entry
- LaTeX-special escaping of PDF prose (`$4.24`, `98.87%`, etc.)
- ACM render path plus the full Comp harmonize / validate / compile cycle
- MiKTeX-compatible compile status detection

### Reproducing locally

```bash
python run_regressions.py
```

Writes per-run artefacts under `build/regressions/<regression>/` and a combined
`summary.json` at the regression root.
