# 3-Agent Paper Conversion System

Agents:
- April — IEEE → ACM conversion
- Friday — ACM → IEEE conversion
- Comp — orchestration, validation, compile, harmonization

This system uses a Canonical Paper Representation (CPR) intermediate format for both directions:

1. Parse source project into CPR
2. Render CPR into target template
3. Harmonize, validate, and compile via Comp

## Quick start

```bash
python -m paper_conversion_system.cli convert \
  --source-format ieee \
  --target-format acm \
  --input ./samples/ieee_sample \
  --workdir ./build/job1
```

Or:

```bash
python -m paper_conversion_system.cli convert \
  --source-format acm \
  --target-format ieee \
  --input ./samples/acm_sample \
  --workdir ./build/job2
```

## Output

A JSON report with:
- `job_id`
- `direction`
- `status`
- `converted_source_path`
- `final_pdf_path`
- `validation`
- `reports`

## Notes

- Compile is attempted with `pdflatex` and `bibtex` when available.
- If LaTeX tooling is unavailable, Comp returns structured warnings/failure details.
- Automated repair attempts are capped at 2.

## System prompts

### April system prompt
You are April, an IEEE to ACM conversion specialist. Convert IEEE manuscripts into ACM-compliant LaTeX while preserving technical meaning. Work through CPR normalization first, then template generation. Return both converted source and a conversion report listing all mappings, assumptions, and unresolved issues.

### Friday system prompt
You are Friday, an ACM to IEEE conversion specialist. Convert ACM manuscripts into IEEE-compliant LaTeX while preserving technical meaning. Work through CPR normalization first, then template generation. Return both converted source and a conversion report listing all mappings, assumptions, and unresolved issues.

### Comp system prompt
You are Comp, the orchestration and compile agent. Take converted output from April or Friday, harmonize context for seamless reading, validate template/citation/structure constraints, compile resources into final artifacts, and return a complete validation and compile report. Do not alter scientific claims; only improve conformity, consistency, and build reliability.

## Definition of done

Done means:
- April, Friday, and Comp are implemented and wired into one pipeline.
- Both directions pass integration tests on sample manuscripts.
- Comp generates final PDF and structured reports.
- Failures are actionable, logged, and reproducible.

Current status in this workspace:
- Implemented and wired: yes.
- Structured reports and reproducible failures: yes.
- Final PDF generation: blocked until `pdflatex`/`bibtex` are installed.
- Integration test execution via pytest: blocked until `pytest` is installed.
