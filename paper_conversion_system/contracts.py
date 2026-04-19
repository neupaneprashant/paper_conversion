from __future__ import annotations

APRIL_INPUT_CONTRACT = {
    "source_format": ["ieee_latex_project", "cpr"],
    "target": "acm_latex_project",
    "required": ["input_path_or_cpr"],
}

APRIL_OUTPUT_CONTRACT = {
    "artifacts": ["converted_acm_latex_project", "conversion_report"],
}

FRIDAY_INPUT_CONTRACT = {
    "source_format": ["acm_latex_project", "cpr"],
    "target": "ieee_latex_project",
    "required": ["input_path_or_cpr"],
}

FRIDAY_OUTPUT_CONTRACT = {
    "artifacts": ["converted_ieee_latex_project", "conversion_report"],
}

COMP_INPUT_CONTRACT = {
    "required": [
        "target_direction_metadata",
        "converted_project_path",
        "original_cpr",
    ]
}

COMP_OUTPUT_CONTRACT = {
    "artifacts": [
        "final_harmonized_project",
        "compile_artifacts",
        "validation_report",
    ]
}
