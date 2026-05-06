# Project Update - 2026-04-29

## Scope

This update summarizes the code changes currently present in the workspace on branch `codex/stabilize-preamble-fidelity`. It reflects the modified and newly added files that affect conversion quality, job execution stability, logging, PDF ingestion, and UI job monitoring.

## High-Level Outcome

The current work moves the project in four major directions:

1. Better conversion fidelity for source LaTeX projects by preserving more source context and specializing agent behavior.
2. Safer and more stable job execution by moving more responsibility into isolated workers with better job metadata and timeout handling.
3. Better observability through structured logging and richer UI job reporting.
4. Stronger PDF ingestion through optional backend selection, including `pdfplumber` table extraction and an initial GROBID integration path.

## Changed Files

### `paper_conversion_system/agents.py`

This file was extended to make the route agents do real preprocessing instead of acting as thin wrappers around the shared normalization layer.

#### What changed

- Added `_active_notes` to track agent-generated warnings and explanations during conversion.
- Added `_agent_preprocess(...)` hook to the base conversion agent so each direction-specific agent can modify CPR before normalization.
- Updated conversion report assembly so agent notes are included in warnings and agent assumptions are included in the report assumptions.

#### April (`IEEE -> ACM`)

- Added IEEE author-block parsing from source LaTeX.
- Parses `\IEEEauthorblockN` and `\IEEEauthorblockA` into structured `author_profiles`.
- Extracts names, institution strings, and emails from IEEE frontmatter.
- Pushes those structured values back into CPR metadata before ACM rendering.

#### Why this matters

- IEEE papers often encode multiple authors inside grouped author blocks that were previously flattened or misread.
- ACM output now has a better chance of generating proper `\author`, `\affiliation`, and `\email` blocks instead of merged or misplaced author metadata.

#### Friday (`ACM -> IEEE`)

- Added preprocessing that strips ACM ceremony macros from preserved preambles.
- Removes items like:
  - `\setcopyright`
  - `\acmConference`
  - `\acmDOI`
  - `\acmISBN`
  - `\acmPrice`
  - year / received metadata variants
- Promotes conference metadata into `ieee_conference_header` for downstream IEEE rendering.

#### Why this matters

- Prevents ACM-only metadata from leaking into IEEE output.
- Gives the renderer an IEEE-specific hint for conference header material instead of blindly carrying ACM frontmatter through.

### `paper_conversion_system/api.py`

This file received some of the most important stability work. The API server is now more resilient and more informative.

#### What changed

- Replaced `print(...)`-based request logging with `logging`.
- Wired in centralized logging via `configure_logging("api")`.
- Added worker environment isolation through `_build_worker_env(...)`.
- Added worker launch logging.
- Added SSE support for live job updates.
- Added CLI flags for log formatting and log level.
- Added `conversion_method` into job metadata at creation time.

#### Worker isolation updates

- Worker subprocesses no longer inherit the full parent environment by default.
- Default behavior is now allowlist-based environment propagation.
- `PAPER_CONVERSION_*` and `OPENCLAW_*` settings are preserved intentionally.
- Proxy variables are cleared by default when `PAPER_CONVERSION_RESTRICT_NETWORK=1`.
- Worker receives `PAPER_CONVERSION_JOB_DIR`.
- Worker receives explicit `PYTHONPATH` rooted at the workspace.
- Worker subprocess now launches with `cwd=job_dir` instead of the whole workspace.
- On Windows, worker launch uses `CREATE_NEW_PROCESS_GROUP`.

#### SSE updates

- Added `/api/jobs/{id}/events`.
- Server pushes:
  - `job`
  - `heartbeat`
  - `done`
  - `error`
- This reduces UI dependence on repeated polling and gives more responsive job-state updates.

#### Logging updates

- Added argument parsing at process startup:
  - `--host`
  - `--port`
  - `--log-format`
  - `--log-level`
- Server startup now logs through the structured logger rather than printing directly.

### `paper_conversion_system/job_worker.py`

This file was updated to align worker execution with the new logging and reporting model.

#### What changed

- Replaced local logging setup with centralized `configure_logging("worker")`.
- Worker now writes `conversion_method` into final job metadata.
- Existing job lifecycle behavior remains, but now better reflects whether a run was LLM-based or local.

#### Why this matters

- Worker and API logs now share one consistent logging model.
- Job detail pages and downstream diagnostics can tell whether the run used:
  - `llm`
  - `local`

### `paper_conversion_system/models.py`

This file received a small but important metadata extension.

#### What changed

- Added `conversion_method` to `JobOutput`.
- Added `conversion_method` to `JobOutput.to_dict()`.

#### Why this matters

- Jobs can now explicitly report how the conversion happened.
- This removes ambiguity when OpenClaw LLM conversion falls back to the local pipeline.

### `paper_conversion_system/orchestrator.py`

This file was updated to make conversion routing and reporting more transparent.

#### What changed

- Added `conversion_method` tracking across orchestration flow.
- Added `fallback_reason` tracking when the LLM path is unavailable.
- Switched LLM fallback output from `print(...)` to structured logger warnings.
- Ensured local fallback paths append a warning into the conversion report.
- Passed `conversion_method` into `Comp.process(...)`.

#### Behavior changes

- If archive inputs force the local path, that reason is tracked.
- If the LLM times out, that reason is tracked.
- If the LLM errors, that reason is tracked.
- If the LaTeX input is too large for LLM context, that reason is tracked.

#### Why this matters

- Users can now distinguish successful LLM conversions from local fallbacks.
- The project now exposes a more honest explanation of why different runs can behave differently.

### `paper_conversion_system/pdf_ingest.py`

This is the main PDF ingestion upgrade area and one of the most important changes in this batch.

#### What changed

- Added backend selection through `PAPER_CONVERSION_PDF_BACKEND`.
- Added `pdf_backend` metadata to the CPR.
- Added optional GROBID fulltext ingestion path.
- Added optional `pdfplumber` table extraction path.
- Added logging around PDF backend behavior.

#### GROBID integration

When:

- `PAPER_CONVERSION_PDF_BACKEND=grobid`
- `PAPER_CONVERSION_GROBID_URL` is set

the parser now:

- POSTs the PDF to `/api/processFulltextDocument`
- Parses returned TEI XML
- Extracts:
  - title
  - authors
  - abstract
  - section blocks
  - references

If GROBID fails or returns invalid XML, the system logs the issue and falls back instead of crashing.

#### `pdfplumber` table extraction

- Added `_extract_tables_with_pdfplumber(...)`.
- Runs in `pdfplumber` mode and also in `heuristic` mode as a supplemental pass.
- Reconstructs basic LaTeX tabular output from extracted PDF table rows.
- Uses simple escaping to avoid breaking generated LaTeX.

#### Why this matters

- PDF ingest was one of the weakest parts of the pipeline.
- `pdfplumber` improves table recovery without requiring a separate external service.
- GROBID adds a path toward layout-aware PDF understanding instead of relying entirely on OCR-like heuristics.

### `paper_conversion_system/render.py`

This file received a targeted rendering improvement for IEEE output.

#### What changed

- Added IEEE-specific extra frontmatter rendering.
- Emits `\IEEEoverridecommandlockouts`.
- Emits a conference comment line when conference metadata is available.

#### Why this matters

- IEEE output now has a cleaner venue-specific setup.
- Conference metadata gathered earlier in the pipeline can now influence the rendered result in a safer way.

### `paper_conversion_system/logging_utils.py`

This is a new file.

#### What it adds

- `JsonFormatter`
- `configure_logging(service: str)`

#### JSON log behavior

When `PAPER_CONVERSION_LOG_FORMAT=json` or `--log-format json` is used, logs can now emit structured JSON containing:

- `ts`
- `level`
- `service`
- `logger`
- `msg`

and optional structured fields when attached to a record:

- `job_id`
- `stage`
- `duration_ms`
- `worker_pid`
- `failure_kind`

#### Why this matters

- Logs are now machine-readable.
- This makes the server much easier to pipe into external tooling and job diagnostics.

### `ui/job.html`

This file was updated so the job page handles both richer metadata and temporary engine failures better.

#### What changed

- Job detail now shows:
  - timeout
  - conversion method
- Added SSE job subscription logic using `EventSource`.
- Added polling fallback when SSE is unavailable or disconnected.
- Added explicit engine-unreachable messaging in the banner.
- Prevents endless polling after terminal job states.

#### Why this matters

- The UI is more responsive during normal runs.
- The UI behaves more gracefully when the engine is down or restarts.
- Users can now see whether the job ran via local pipeline or LLM path.

### `tests/test_job_runtime.py`

This file gained additional coverage around the new runtime behavior.

#### Added tests

- `test_worker_env_is_restricted_by_default`
  - Verifies sensitive env vars like `OPENAI_API_KEY` are not leaked into workers by default.
  - Verifies `PAPER_CONVERSION_JOB_DIR` is present.
  - Verifies proxy/network restriction settings are applied.

- `test_json_logging_mode_configures_handler`
  - Verifies JSON logging mode can initialize correctly.

#### Existing runtime tests retained

- Bounded equation cleanup timing.
- Stale running job recovery.
- Worker timeout behavior while keeping the server responsive.

### `tests/test_agent_specialization.py`

This is a new test file.

#### What it validates

- April correctly translates IEEE author blocks into structured ACM author output.
- Friday strips ACM ceremony macros from output destined for IEEE.
- Friday still emits IEEE-specific header setup.

#### Why this matters

- The new specialized behavior is now covered by tests rather than relying only on manual checks.

### `tests/snapshots/arxiv_diffusion_acm/main.tex`
### `tests/snapshots/arxiv_graph_acm/main.tex`

These snapshot files were updated to reflect renderer changes.

#### What changed

- Snapshot outputs now include `\IEEEoverridecommandlockouts`.

#### Why this matters

- Snapshot expectations were realigned with the new IEEE frontmatter rendering behavior.

## Validation Performed

The following checks were run during this update cycle:

- `python -m compileall -q paper_conversion_system tests ui`
- `python -m pytest tests\\test_job_runtime.py tests\\test_agent_specialization.py tests\\test_snapshot_regressions.py -q`
- `python -m pytest tests\\test_job_runtime.py -q`

Recent reported results from those runs were passing, including:

- `13 passed` for the combined runtime / specialization / snapshot set
- `5 passed` for the focused runtime check after the PDF ingest additions

## Notable Behavioral Improvements

- Job runs now expose whether they used the LLM path or local fallback.
- Worker jobs are more isolated from the parent process environment.
- The API can stream job state changes instead of relying entirely on polling.
- The UI now degrades more gracefully if the stream breaks or the server becomes unreachable.
- PDF ingest has a concrete path toward structured extraction rather than only heuristic extraction.

## Known Limits / Follow-Up Work

The current changes improve the architecture, but a few items still remain incomplete or partial:

- GROBID support is present but depends on an external running GROBID service.
- `marker` is still only declared as a backend option; it is not wired in yet.
- `pdfplumber` table extraction is useful, but it is still a heuristic reconstruction rather than a full layout model.
- The UI still contains some mojibake / encoding artifacts in static text and could use a cleanup pass.
- There are unrelated workspace items still outside this summary, including untracked design files, temp folders, and `.claude/worktrees` content.

## Files Intentionally Not Covered as Core Product Changes

These are present in the workspace but are not part of the conversion-engine change summary above:

- `.claude/worktrees/...`
- `components/`
- `design-canvas.jsx`
- `Paper Parser Landing.html`
- `Paper Parser Landing - bundle src.html`
- `tmp/...`

They appear to be workspace artifacts, experiments, or separate design work rather than the core conversion pipeline updates described here.

## Recommended Next Steps

1. Run one live PDF and one live ZIP conversion against the restarted server to confirm:
   - worker launch
   - job status streaming
   - conversion method reporting
   - PDF backend behavior
2. If GROBID is available on this machine, test `PAPER_CONVERSION_PDF_BACKEND=grobid` on a problem PDF and compare section/reference recovery against heuristic mode.
3. Clean up UI text encoding artifacts in `ui/job.html`.
4. Add a dedicated integration test for GROBID fallback behavior so the backend path is exercised safely even when the service is unavailable.
