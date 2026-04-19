# Academic Paper Converter - Product & Technical Specification

## Vision

Build a bidirectional academic paper conversion system that transforms manuscripts between IEEE and ACM LaTeX formats with high semantic fidelity, structured validation, compile orchestration, and reproducible diagnostics.

Core agents:
- **April** — IEEE → ACM conversion specialist
- **Friday** — ACM → IEEE conversion specialist
- **Comp** — orchestration, harmonization, validation, compile, packaging

## Primary goals

1. Preserve scientific meaning exactly
2. Convert through a canonical intermediate representation (CPR)
3. Produce target-native output that reads as if authored for the target venue
4. Generate structured reports and trace logs for every run
5. Support actionable failure recovery when compile/validation fails

## System architecture

```text
Input Project/Paper
   -> Ingest & Detect
   -> Parse into CPR
   -> Direction Router
      -> April (IEEE -> ACM) OR Friday (ACM -> IEEE)
   -> Converted Target Project
   -> Comp
      -> Harmonization
      -> Validation
      -> Compile / Retry / Repair
      -> Packaging
   -> Final Deliverables + Reports
```

## Canonical Paper Representation (CPR)

CPR should evolve into a richer schema with these domains:

### Metadata
- title
- subtitle
- authors
- affiliations
- author notes
- corresponding author
- emails
- ORCID ids
- keywords
- CCS concepts
- venue metadata
- funding statement
- conflict-of-interest statement
- acknowledgments

### Content structure
- abstract
- teaser / graphical abstract
- sections
- subsections
- appendices
- footnotes
- theorem/lemma/proof blocks
- equations
- algorithms
- figures
- tables
- listings/code blocks

### Reference structure
- bibliography entries
- citation commands
- citation style intent
- unresolved citation placeholders

### Asset structure
- figure files
- table fragments
- bibliography files
- class/style dependencies

## Production-quality architecture tightening

### 1. Clear layered boundaries
- `ingest/` — file import, type detection, unpacking
- `cpr/` — CPR schema + parsers + serializers
- `convert/` — April/Friday transforms from CPR to target template
- `validate/` — structural/template/semantic checks
- `compile/` — build orchestration and repair logic
- `reports/` — JSON reports, logs, trace snapshots
- `ui/` — web frontend
- `runtime/` — OpenClaw session integration

### 2. Deterministic contracts
Every stage must define:
- required inputs
- produced artifacts
- warnings/errors schema
- retry eligibility
- idempotent output directory layout

### 3. Stable job layout

```text
jobs/<job_id>/
  input/
  normalized/
  converted/
  final/
  artifacts/
  reports/
    conversion_report.json
    harmonization_report.json
    compile_report.json
    validation_report.json
    trace.json
```

### 4. Validation levels
- **L1** Syntax checks
- **L2** Template structure checks
- **L3** Cross-reference checks
- **L4** Metadata fidelity checks
- **L5** Semantic drift review hints

### 5. Compile strategy
- detect toolchain availability
- run ordered passes
- capture exact commands and logs
- retry up to 2 times for safe repairs only
- preserve failing workspace for reproduction

## True OpenClaw multi-agent execution design

Instead of plain Python classes only, support actual OpenClaw agent sessions:

### Agent session model
- **April session**: dedicated conversion specialist thread
- **Friday session**: dedicated conversion specialist thread
- **Comp session**: orchestration/validator thread

### Session workflow
1. Main controller receives job request
2. Controller determines direction
3. Controller spawns/sends work to April or Friday
4. Conversion agent returns converted project + report
5. Controller forwards outputs and CPR to Comp
6. Comp validates, compiles, harmonizes, and packages outputs
7. Controller returns final job object to UI/API

### Runtime requirements
- thread-bound persistent sessions for each agent persona
- stable message schema for attachments and reports
- explicit artifact handoff paths
- structured status streaming back to UI

## UI product spec

## Core screens

### 1. Home / Convert screen
Sections:
- Hero title: **Academic Paper Converter**
- Subtitle: transform papers between ACM and IEEE with AI-assisted precision
- Upload panel
- Direction selector
- Advanced options accordion
- Convert button
- How it works panel
- Recent jobs / status list

### 2. Job detail screen
Sections:
- Job status header
- Step progress timeline (Ingest → CPR → Convert → Harmonize → Validate → Compile)
- Download cards
  - converted source
  - final PDF
  - reports zip
- Validation summary
- Warnings/errors panel
- Expandable trace/log viewer

### 3. Agent activity panel
Show:
- April status
- Friday status
- Comp status
- current action
- warnings count
- elapsed time

## UX notes
- clean white background
- subtle blue/purple gradient CTA
- rounded cards
- drag/drop upload
- clear unsupported-direction errors
- progress states that feel alive but not noisy
- reports accessible without overwhelming the user

## API shape for UI/backend

### Submit job
`POST /api/jobs`

Request:
```json
{
  "source_format": "ieee",
  "target_format": "acm",
  "input_path": "..."
}
```

Response:
```json
{
  "job_id": "job-...",
  "status": "queued"
}
```

### Get job
`GET /api/jobs/:job_id`

Response includes the required output schema:
```json
{
  "job_id": "job-...",
  "direction": "ieee_to_acm",
  "status": "success",
  "converted_source_path": "...",
  "final_pdf_path": "...",
  "validation": {
    "template_compliance": "pass",
    "citation_compliance": "pass",
    "compile_status": "success",
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

## Definition of Done
- April, Friday, and Comp implemented as first-class agents
- Both conversion directions verified on sample manuscripts
- Final PDF artifacts generated when toolchain is present
- Structured reports and logs emitted for every run
- Clean UI available for upload, progress, and downloads
- Failures are reproducible and actionable
