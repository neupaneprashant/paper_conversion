# IEEEtoACM Workflow

This app does not claim to expose hidden chain-of-thought.

Instead, it externalizes a reviewable workflow that mirrors the paper-conversion process in a way you can audit.

## Stages

1. Inventory the input.
2. If the input is a PDF, render the PDF pages for visual review.
3. Build a preflight CPR summary from the PDF ingest path.
4. Run the repo's existing IEEE -> ACM conversion pipeline.
5. Compile and validate through `Comp`.
6. If an output PDF exists, render the output pages for review.
7. Write `workflow.json` and `workflow.md`.

## Why this exists

The current repo already converts papers. What it did not expose well enough was the visible review path around the conversion. This app adds that missing application layer without replacing the existing engine.

## Review artifacts

The app saves:

- input inventory
- rendered input pages
- CPR summary
- conversion result summary
- rendered output pages
- manifest of generated files

## Fidelity note

For LaTeX-source inputs, the engine is strongest.

For PDF-only inputs, this remains a reconstruction workflow. The app makes that workflow easier to inspect, but it does not magically turn a PDF into perfect source.
