# Conversion Requirements

## Non-negotiable requirements
- Always convert through CPR.
- Never invent claims, results, data, citations, author identities, affiliations, or funding details.
- Preserve semantic equivalence of equations, figures, tables, section meaning, and bibliography intent.
- Emit warnings when the target template cannot represent a source construct directly.
- Prefer target-native style over source-style macro mimicry.

## April requirements (IEEE -> ACM)
- Accept IEEE LaTeX project or CPR.
- Normalize IEEE frontmatter into CPR fields.
- Render output with ACM `acmart` conventions.
- Convert `IEEEkeywords` to `\\keywords{}`.
- If ACM-native metadata like CCS concepts is unavailable, omit and warn only when source appears to contain taxonomy-like content.
- Map acknowledgments into `acks` environment when explicit acknowledgment text is present.
- Keep abstract before `\\maketitle`.
- Preserve bibliography references and citation commands.

## Friday requirements (ACM -> IEEE)
- Accept ACM LaTeX project or CPR.
- Normalize ACM frontmatter into CPR fields.
- Render output with IEEE `IEEEtran` conventions.
- Convert ACM `\\keywords{}` to `IEEEkeywords`.
- If source contains CCS concepts, emit warning and omit unless a deliberate fallback strategy is defined.
- Route acknowledgments/funding/conflict text to a safe IEEE-compatible unnumbered section fallback.
- Keep abstract and `IEEEkeywords` in IEEE-compatible placement.
- Preserve bibliography references and citation commands.

## Frontmatter mapping requirements
- Title must be preserved exactly except whitespace cleanup.
- Author names must not be invented or reordered unless source order is clearly recoverable and preserved.
- Missing affiliations/emails must not be fabricated.
- Use neutral placeholders only when source is already incomplete.

## Structural requirements
- Required sections must be validated by target profile.
- Section ordering should be target-native where possible without changing meaning.
- Figure/table labels and refs must remain resolvable.
- Broken LaTeX commands must be surfaced.

## Reporting requirements
Every conversion report must include:
- mapped_fields
- changed_sections
- unresolved_items
- warnings
- assumptions
- target_template_profile
