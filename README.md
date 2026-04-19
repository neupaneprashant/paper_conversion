# paper_conversion

`paper_conversion` is a local-first academic paper conversion system that translates manuscripts between ACM and IEEE formats with a three-agent workflow:

- `April`: IEEE to ACM conversion
- `Friday`: ACM to IEEE conversion
- `Comp`: harmonization, validation, compile orchestration, and packaging

The pipeline uses a Canonical Paper Representation (CPR) as an intermediate format so both conversion directions share the same normalization and rendering flow.

## Features

- Convert ACM LaTeX projects to IEEE
- Convert IEEE LaTeX projects to ACM
- Ingest PDFs into LaTeX before format conversion
- Produce structured reports and packaged job artifacts
- Run local regression checks without relying on `pytest`

## Quick Start

```bash
python -m paper_conversion_system.cli convert \
  --source-format ieee \
  --target-format acm \
  --input ./samples/ieee_sample \
  --workdir ./build/job1
```

```bash
python run_regressions.py
```

## Project Layout

- `paper_conversion_system/`: core pipeline, CLI, API, validators, and renderers
- `samples/`: example ACM and IEEE LaTeX projects
- `tests/`: focused regression and integration tests
- `ui/`: static frontend scaffold for a future local API workflow

## Notes

- LaTeX compilation succeeds when `pdflatex` and `bibtex` are available locally.
- `run_regressions.py` provides an in-repo verification path even when `pytest` is not installed globally.
- Additional implementation and delivery notes live in the top-level markdown docs.
