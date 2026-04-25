# April / Friday Mapping Rules

## System-level rules
- Use CPR as the only supported semantic handoff between ingest and render.
- Treat LaTeX-source extraction as high confidence.
- Treat PDF-derived metadata, especially frontmatter, as lower confidence.
- Treat thesis/dissertation inputs as alternate degraded-mode sources.
- Prefer explicit warnings over fabricated completion of missing context.
- Keep outputs suitable for downstream website/API/OpenClaw orchestration.
- For PDF inputs, support a fidelity mode that prioritizes visible preservation over editability.

## April (IEEE -> ACM)

### Frontmatter
- Preserve `\\title{}` meaning exactly.
- Convert IEEE author list into ACM-compatible `\\author{}` / affiliation / email structure only when supported by CPR metadata.
- Convert `IEEEkeywords` into `\\keywords{}`.
- If source contains ACM-style metadata already, preserve only what is semantically valid in CPR.
- Do not fabricate CCSXML, affiliations, email addresses, conference metadata, or acknowledgments details.
- Escape frontmatter safely so affiliations like `A&T` do not break ACM compilation.

### Metadata
- If acknowledgments exist, render them in `acks`.
- If CCS concepts exist with CCSXML, render them in ACM output.
- If only partial CCS data exists, warn and omit incomplete block.
- If PDF extraction produced uncertain frontmatter, preserve only grounded fields and emit warnings for the rest.

### Body
- Preserve section titles unless normalization is needed for obvious canonical aliases.
- Preserve equations/refs/figures/tables verbatim when present in section content.
- Do not rewrite claims for style.
- Never let IEEE-only macros or boilerplate leak into final ACM output when they violate template compliance.

### References
- Preserve bibliography intent.
- Never fabricate missing bib entries.
- Prefer partial but honest reference output over confident garbage.
- For PDF inputs with noisy reference parsing, prefer `thebibliography` fallback over fragile auto-generated BibTeX.

## Friday (ACM -> IEEE)

### Frontmatter
- Preserve `\\title{}` meaning exactly.
- Convert ACM keywords to `IEEEkeywords`.
- Omit CCS concepts from IEEE output with explicit warning unless a future mapping policy is added.
- Never invent affiliations, email blocks, or IEEE-only metadata not present in CPR.

### Metadata
- Route acknowledgments to unnumbered `Acknowledgments` section.
- Preserve only grounded author/affiliation structure.
- If PDF extraction produced uncertain frontmatter, preserve only grounded fields and emit warnings for the rest.
- Accept anchored visual fallback for tables/equations when structured recovery is weak.

### Body
- Preserve section semantics and references.
- Keep LaTeX body content as stable as possible; prefer structural wrapping changes over content rewriting.
- Never let ACM-only macros or acmart-specific frontmatter leak into final IEEE output when they violate template compliance.

### References
- Preserve bibliography intent.
- Never fabricate missing bib entries.
- Prefer partial but honest reference output over confident garbage.
- For PDF fidelity mode, preserve reference text in `thebibliography` if BibTeX confidence is weak.

## Comp quality gate rules
- Validate L1 skeleton sanity, L2 template compliance, L2 citation compliance, and L3 cross-reference integrity.
- Harmonize phrasing only when meaning is preserved exactly.
- Repair only narrow mechanical LaTeX issues.
- Never hide degraded confidence from PDF/thesis ingest.
- Produce traceable reports that explain what was preserved, changed, unresolved, warned, compiled, and packaged.
- Prefer anchored cropped artifacts for low-confidence tables/equations/figures instead of silently omitting them.
