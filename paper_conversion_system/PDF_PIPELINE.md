# PDF to LaTeX Conversion Pipeline

This document describes the integrated PDF-to-LaTeX pipeline used by the paper conversion system.

## Overview

The current pipeline is CPR-first and preservation-oriented:

1. **Stage 1: PDF Text Extraction** (`pdf_parser.py`)
   - Tries multiple extraction methods in order of preference:
     - `pdfplumber`
     - `pypdf`
     - `fitz` (PyMuPDF)
     - OCR via Tesseract for scanned PDFs
   - Reports extraction method and confidence metadata

2. **Stage 2: CPR-Oriented PDF Analysis** (`pdf_ingest.py`, `pdf_postprocess.py`)
   - Detects:
     - titles and author information
     - abstract and keywords
     - section headings
     - bibliographic references
     - figures, tables, and display equations using PyMuPDF layout data
     - nearby paragraph anchors for later reinsertion
   - Returns CPR plus artifact metadata and preservation maps

3. **Stage 3: Target Rendering** (`render.py`, `templates.py`)
   - Generates ACM or IEEE LaTeX from CPR
   - Handles:
     - target-native frontmatter mapping
     - anchored figure/table/equation insertion
     - `thebibliography` fallback for noisy PDF references
     - safe frontmatter/body escaping for LaTeX compatibility

## Preservation Strategy

For PDF inputs, the system now uses a hybrid policy:

1. **Editable when reliable**
   - Reconstruct references/tables when confidence is acceptable.

2. **Visual fallback when uncertain**
   - Crop figures, tables, and equations with PyMuPDF.
   - Reinsert them near matched paragraph anchors instead of dropping them.

3. **Reference honesty first**
   - Prefer `thebibliography` over invented BibTeX fields when PDF reference parsing is weak.

4. **Fidelity mode**
   - Prioritize retaining visible content over forcing every object into editable LaTeX.

## Usage

```bash
python -m paper_conversion_system convert \
  --source-format ieee \
  --target-format acm \
  --input paper.pdf \
  --workdir ./job_out \
  --fidelity-mode preserve
```

`preserve` is the recommended mode for PDF inputs. Use `editable` only when you want the system to lean harder toward reconstructed LaTeX even if that may lose fidelity.

## Current Limitations

- OCR accuracy still depends on PDF quality.
- Complex tables may not reconstruct perfectly.
- Mathematical notation detection is still heuristic, not semantic.
- Multi-column layouts may be flattened during text extraction.
- Anchored artifact reinsertion is heuristic and can still miss the ideal paragraph boundary.

## Testing

Run the focused conversion tests with:

```bash
python -m pytest tests/test_cpr_mapping.py tests/test_integration.py -q
```
