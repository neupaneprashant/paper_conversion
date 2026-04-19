# Project Deliverable: PDF/Text-Based Academic Paper Conversion System

## 1. Project Goal

Build a reliable academic paper conversion system that can convert:

- **IEEE paper → ACM paper**
- **ACM paper → IEEE paper**

from either:
- **source LaTeX projects** when available, or
- **PDF/text-based papers** through a structured extraction pipeline

The final system should produce output that is:

- structurally correct for the target format
- semantically faithful to the original paper
- readable as if written natively in the target template
- auditable through structured reports
- robust enough to handle noisy PDF input with graceful degradation

---

## 2. Core Objective

The system must convert academic manuscripts between IEEE and ACM formats by using:

1. **document ingestion**
2. **canonical structural normalization**
3. **direction-specific conversion agents**
4. **post-conversion validation**
5. **artifact packaging**

This is not just a syntax transformer.

It is a **semantic paper conversion pipeline**.

---

## 3. Named Agents

These names are fixed and must remain exact:

- **April**
  - IEEE → ACM conversion specialist

- **Friday**
  - ACM → IEEE conversion specialist

- **Comp**
  - orchestration, harmonization, validation, compile, and packaging agent

---

## 4. Final Product Behavior

### 4.1 Supported directions
- **IEEE → ACM**
- **ACM → IEEE**

### 4.2 Supported source forms
- **LaTeX project input**
  - preferred and highest fidelity
- **PDF/text-based input**
  - supported through an extraction + cleanup + parsing pipeline

### 4.3 Required output
For every job, return:

- converted LaTeX source project
- structured conversion report
- validation report
- compile report
- packaged artifacts
- final PDF if compile succeeds

---

## 5. Required Architecture

## 5.1 Two-step conversion principle
All conversions must use a **Canonical Paper Representation (CPR)**.

Pipeline:

```text
source input
→ parse/extract into CPR
→ normalize CPR
→ render CPR to target template
→ validate / harmonize / compile
→ package outputs
```

No brittle direct template-to-template rewriting should be the primary strategy.

---

## 5.2 Input routing

### For LaTeX source
```text
LaTeX source
→ LaTeX parser
→ CPR
→ April or Friday
→ Comp
```

### For PDF/text-based source
```text
PDF
→ document-type detection
→ safe cleanup
→ parser path selection
    ├─ paper parser
    └─ thesis/dissertation alternate parser
→ CPR
→ postprocess/refine CPR
→ April or Friday
→ Comp
```

---

## 6. Conversion Directions

## 6.1 IEEE → ACM
Route:
```text
IEEE input
→ CPR
→ April
→ ACM LaTeX
→ Comp
```

April responsibilities:
- preserve title, authors, abstract, sections, references
- convert IEEE keywords to ACM keywords
- map metadata into ACM `acmart` conventions
- surface unresolved ACM-only metadata gaps as warnings

---

## 6.2 ACM → IEEE
Route:
```text
ACM input
→ CPR
→ Friday
→ IEEE LaTeX
→ Comp
```

Friday responsibilities:
- preserve title, authors, abstract, sections, references
- convert ACM keywords to IEEE keywords
- omit or warn on ACM-only metadata without IEEE equivalent
- render IEEEtran-compatible output

---

## 7. Canonical Paper Representation (CPR)

The CPR is the core intermediate schema.

It must support at least:

### Metadata
- title
- authors
- affiliations
- emails
- abstract
- keywords
- acknowledgments
- optional CCS concepts
- optional source metadata
- document type
- ingest confidence
- warnings

### Structure
- sections
- subsection cues
- figures
- tables
- references

### Artifacts
- source path
- page count
- cleanup metadata
- parser path used

---

## 8. PDF/Text-Based Conversion Requirements

This is the critical deliverable area.

The project must support **PDF/text-based conversion** robustly enough that the system does not collapse into:
- 2-line outputs
- broken titles
- fake structure
- garbage frontmatter

---

## 8.1 PDF ingestion pipeline
Required components:

### A. Document-type detection
System must classify at minimum:
- `ieee_paper`
- `acm_paper`
- `thesis_dissertation`
- `generic_academic_pdf`

Purpose:
- avoid misparsing theses as conference papers
- route to appropriate parser behavior

### B. Safe cleanup pass
Default cleanup must remove:
- repeated headers/footers
- license/download notices
- DOI clutter
- obvious conference/page boilerplate
- unicode/hyphenation/spacing artifacts

It must **preserve structure**.

### C. Optional aggressive cleanup
Aggressive cleanup must:
- only be used after CPR confidence checks
- strip stronger figure/table/layout noise
- never be the default blind pass

### D. CPR refinement
After raw extraction, the system must refine CPR by attempting:
- frontmatter cleanup
- reference isolation
- figure/table detection
- subsection heuristics
- confidence scoring

---

## 8.2 Thesis/dissertation alternate path
The system must support a separate parser path for thesis/dissertation PDFs.

### Why
Thesis/dissertation documents contain:
- title pages
- committee pages
- acknowledgments
- tables of contents
- lists of figures/tables
- chapter-oriented structure

These will corrupt a paper-first parser.

### Requirements
The thesis parser must:
- detect thesis-style documents
- extract title more safely
- extract author more safely
- extract abstract
- strip TOC/list contamination
- identify chapter-oriented sections
- parse references from the true bibliography area
- emit warnings that thesis parsing is an alternate degraded mode

### Important behavior
If thesis parsing is still not reliable enough for production use, the system must:
- degrade gracefully
- warn explicitly
- never confidently present garbage as high-quality conversion

---

## 9. April / Friday Functional Requirements

## 9.1 April
April must become expert in:
- IEEE paper structure
- IEEE title/author block patterns
- IEEE abstract + index terms patterns
- IEEE section ordering expectations
- IEEE figure/table/reference conventions
- ACM `acmart` target conventions

April output must include:
- converted ACM LaTeX project
- structured conversion report
- unresolved items
- assumptions
- warnings
- target template profile

---

## 9.2 Friday
Friday must become expert in:
- ACM paper structure
- ACM frontmatter patterns
- ACM author/affiliation/email conventions
- ACM keywords / CCS patterns
- ACM reference/caption conventions
- IEEE target conventions

Friday output must include:
- converted IEEE LaTeX project
- structured conversion report
- unresolved items
- assumptions
- warnings
- target template profile

---

## 10. Comp Functional Requirements

Comp must:
- harmonize output
- validate structure
- validate references/citations
- validate format-specific expectations
- compile if toolchain is available
- package outputs
- return structured reports

Comp is the final quality gate.

### Comp checks must include
- required section presence
- citation/bibliography presence
- unresolved references
- target-specific forbidden pattern leakage
- no broken LaTeX command fragments where detectable
- compile artifact tracking
- warnings surfaced even on success

---

## 11. Output Requirements

Every conversion job must return:

```json
{
  "job_id": "...",
  "direction": "ieee_to_acm | acm_to_ieee",
  "status": "success | failed | partial",
  "converted_source_path": "...",
  "final_pdf_path": "... or null",
  "validation": {
    "template_compliance": "...",
    "citation_compliance": "...",
    "compile_status": "...",
    "warnings": [],
    "errors": []
  },
  "reports": {
    "conversion_report": {},
    "harmonization_report": {},
    "compile_report": {}
  }
}
```

---

## 12. Quality Requirements

The system must prioritize:

### A. Semantic fidelity
Must preserve:
- claims
- results
- equations
- references
- section meaning
- figure/table meaning where recoverable

### B. Graceful degradation
When extraction is weak, the system must:
- warn
- lower confidence
- avoid hallucinating structure

### C. Native-feeling target output
Target output should feel like:
- real ACM paper if converting to ACM
- real IEEE paper if converting to IEEE

Not just a wrapper around extracted text.

---

## 13. Current Known Gaps to Resolve

Cursor should specifically continue addressing these areas:

### PDF paper path
- improve remaining body cleanup
- improve figure/table false-positive filtering
- improve references reconstruction
- improve author-affiliation pairing

### Thesis/dissertation path
- TOC stripping
- chapter heading cleanup
- abstract artifact cleanup
- references chapter parsing
- appendix/list-of-figures/list-of-tables suppression
- better chapter segmentation without collapsing into single `Body`

### General
- better compile reliability
- more robust regression tests
- cleaner final frontmatter rendering
- improved structured bibliography extraction

---

## 14. Deliverables Cursor Should Produce

Cursor should deliver:

### Code deliverables
- improved parser code
- improved cleanup code
- improved renderers
- improved validation logic
- improved thesis parser
- cleaner frontmatter rendering
- stronger bibliography reconstruction

### Documentation deliverables
- updated `documentation.md`
- updated module docstrings/comments
- updated `CHANGES.md`
- updated `DELIVERY_SUMMARY.md` if used

### Testing deliverables
- rerun on at least:
  - one IEEE paper PDF
  - one ACM paper PDF if available
  - one thesis/dissertation PDF
- document what generalizes and what does not
- add or update regression tests where possible

---

## 15. Review Focus for Cursor

Cursor should not just add code.
It should specifically improve:

1. **correctness**
2. **readability**
3. **traceability**
4. **conversion quality**
5. **graceful failure behavior**

---

## 16. Final Expected Outcome

The finished project should be able to do this reliably:

### Best case
- ingest IEEE or ACM LaTeX project
- convert cleanly to target venue
- validate and compile
- return high-quality deliverables

### Good fallback case
- ingest IEEE/ACM-like PDF
- recover enough structure into CPR
- produce a meaningful converted draft
- preserve title/author/abstract/sections/references reasonably well
- warn where fidelity is limited

### Thesis/dissertation case
- detect correctly
- use alternate path
- either:
  - produce a reasonable chapter-oriented converted draft
  - or reject/degrade clearly without pretending it is a clean conference-paper conversion

---

## 17. One-line Summary

**Build a robust multi-agent paper conversion system that converts IEEE ↔ ACM from LaTeX or PDF/text-based input using CPR, document-type-aware parsing, structured validation, and artifact packaging, with graceful degradation on noisy or thesis-style documents.**
