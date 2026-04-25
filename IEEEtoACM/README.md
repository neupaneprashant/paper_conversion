# IEEEtoACM

`IEEEtoACM` is a standalone review app inside this workspace for running the existing IEEE-to-ACM conversion pipeline with a more explicit, auditable workflow.

It is built to give you two things at once:

1. The same local conversion engine already used in this repo.
2. A visible workflow record you can review like an application log instead of hidden model reasoning.

## What is replicated

- The same workspace environment and Python package base.
- The same `paper_conversion_system` conversion pipeline.
- The same IEEE -> ACM direction (`April` + `Comp`) already used by the repo.
- The same PDF ingest, CPR normalization, rendering, validation, and compile flow.

## What is not literally replicable

- Hidden internal chain-of-thought from ChatGPT or Codex is not available to export.
- This app replaces that with an explicit external workflow record:
  - input inventory
  - preflight PDF render
  - CPR summary
  - conversion run summary
  - output render review
  - output manifest

## Folder layout

- `pyproject.toml`: installable app package
- `requirements.txt`: package list for quick review
- `install.ps1`: local setup script
- `run.ps1`: one-command runner
- `WORKFLOW.md`: the replicated review workflow
- `ieee_to_acm_app/`: CLI and workflow code

## Install

```powershell
Set-Location C:\Users\Prash\.openclaw\workspace\IEEEtoACM
.\install.ps1
```

The install script creates `IEEEtoACM\.venv`, installs the root workspace in editable mode with PDF/LaTeX extras, then installs this app in editable mode.

## Run

Convert an IEEE PDF into ACM output with workflow logs:

```powershell
Set-Location C:\Users\Prash\.openclaw\workspace\IEEEtoACM
.\run.ps1 convert --input "C:\Users\Prash\Downloads\Secure_Digital_Signature_Validated_by_Ambient_Users_Wi-Fi-enabled_devices.pdf"
```

Check the environment and installed tools:

```powershell
.\run.ps1 env-check
```

## Output

Each run creates a timestamped folder under `IEEEtoACM\runs\`.

Typical outputs:

- `workflow.json`
- `workflow.md`
- `preflight\cpr_summary.json`
- `review\input_pages\`
- `review\output_pages\`
- `converted\`
- `final\`
- `job_output.json`

## External tools

This app can install the Python packages automatically. System binaries such as `pdflatex`, `bibtex`, and `tesseract` are checked and reported, but not force-installed by the app.
