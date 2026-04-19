# Definition of Done

April, Friday, and Comp are implemented and wired into one pipeline.

Both directions pass integration tests on sample manuscripts.

Comp generates final PDF and structured reports.

Failures are actionable, logged, and reproducible.

## Current status (2026-04-19)

- Pipeline implementation: complete
- Direction routing: complete
- CPR intermediate format: complete
- Structured reports/logging: complete
- Compile retries and actionable failure reporting: complete
- Final PDF generation on this machine: **working** (MiKTeX `pdflatex` detected; `build/regressions/**/final/main.pdf` produced for all three directions)
- Integration test execution on this machine: **working** via `run_regressions.py` (6/6 unit tests pass; pytest itself is not installed because PyPI was unreachable during this pass, but the runner mirrors `tmp_path` fixtures and executes every `test_*` function)
- L1/L2/L3 layered validation: complete
- Target-format leakage detection (IEEE-in-ACM, ACM-in-IEEE): complete
- Thesis/dissertation TOC-vs-body disambiguation: complete (real chapter 1 extracted instead of TOC entry)
- LaTeX-special character escaping for PDF-ingested content: complete (bare `$`, `%`, `&`, `_`, `#` no longer break compile)
- MiKTeX "check for updates" warning no longer flagged as a compile failure

## Regression summary

From `build/regressions/summary.json`:

| Regression | Direction | Template | Citation | Compile | Final PDF |
|---|---|---|---|---|---|
| `samples/ieee_sample` | IEEE -> ACM | pass | pass | success | yes |
| `samples/acm_sample` | ACM -> IEEE | pass | pass | success | yes |
| `ui/Real-Time Prediction ... Hybrid Analog C.pdf` | PDF -> ACM | pass | warn | success | yes |

The PDF regression exercises thesis document-type detection, TOC stripping,
chapter-1 disambiguation, frontmatter recovery, LaTeX-special escaping, the
full render/harmonize/validate/compile pipeline, and structured job output.
