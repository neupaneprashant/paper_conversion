ACM_RULES = {
    "documentclass": "acmart",
    "profile": "acmart-sigconf",
    "keywords_macro": "\\keywords{...}",
    "ccs_macro": "\\begin{CCSXML}...\\end{CCSXML} plus \\ccsdesc",
    "abstract_position": "before maketitle",
    "title_macro": "\\title{...}",
    "author_model": "separate author/affiliation/email blocks preferred",
    "bibliographystyle": "ACM-Reference-Format",
    "bibliography": "\\bibliography{references}",
    "acknowledgments": "\\begin{acks}...\\end{acks}",
    "figure_caption_style": "caption typically below figure",
    "table_caption_style": "caption typically above table in many ACM examples",
    "required_sections": ["Introduction"],
    "special_metadata": ["keywords", "ccs_concepts", "acknowledgments"],
}

IEEE_RULES = {
    "documentclass": "IEEEtran",
    "profile": "IEEEtran-conference",
    "keywords_macro": "\\begin{IEEEkeywords}...\\end{IEEEkeywords}",
    "abstract_position": "after maketitle in conference style",
    "title_macro": "\\title{...}",
    "author_model": "single \\author{...} block often using IEEEauthorblockN/IEEEauthorblockA in conference papers",
    "bibliographystyle": "IEEEtran",
    "bibliography": "\\bibliography{references}",
    "acknowledgments": "unnumbered section or template-specific handling",
    "figure_caption_style": "caption usually below figure",
    "table_caption_style": "caption usually above table",
    "required_sections": ["Introduction"],
    "special_metadata": ["keywords", "acknowledgments"],
}

APRIL_TRAINING_NOTES = [
    "Map IEEE frontmatter into ACM acmart frontmatter.",
    "Convert IEEEkeywords into ACM keywords; if richer taxonomy exists later, support CCS concepts.",
    "Preserve section semantics and references exactly.",
    "Rewrite author blocks toward ACM-native structure when enough metadata exists.",
    "If acknowledgments/funding/conflict text exists, route to ACM-native acks/metadata locations where possible.",
]

FRIDAY_TRAINING_NOTES = [
    "Map ACM acmart frontmatter into IEEEtran-compatible frontmatter.",
    "Convert ACM keywords into IEEEkeywords.",
    "Handle CCS concepts as warning/fallback when there is no strong IEEE-native equivalent.",
    "Preserve section semantics and references exactly.",
    "Route acknowledgments/funding into IEEE-appropriate unnumbered section or warning fallback.",
]
