"""LaTeX generation from structured document data."""

from __future__ import annotations

from typing import Any


def generate_latex_from_structure(structure: dict[str, Any], title: str = "Document") -> str:
    """
    Generate LaTeX document from structured data.

    This generator is intentionally generic and separate from the ACM/IEEE CPR
    renderers. It is mainly useful for exploratory PDF recovery paths where we want
    a readable LaTeX document even before venue-specific conversion is available.
    
    Args:
        structure: Dictionary with 'title', 'sections', 'equations', etc.
        title: Document title (fallback if not in structure)
        
    Returns:
        Complete LaTeX document as string
    """
    
    # Build LaTeX content
    content = _generate_preamble()
    
    # Title page / metadata
    doc_title = structure.get("title") or title
    content += f"\n\\title{{{_sanitize_latex(doc_title)}}}\n"
    content += "\\author{Converted from PDF}\n"
    content += "\\date{\\today}\n\n"
    content += "\\begin{document}\n\n"
    content += "\\maketitle\n\n"
    
    # Abstract if present
    if structure.get("abstract"):
        content += f"\\begin{{abstract}}\n{_sanitize_latex(structure['abstract'])}\n\\end{{abstract}}\n\n"
    
    # Keywords if present
    if structure.get("keywords"):
        keywords = ", ".join(structure["keywords"])
        content += f"\\textbf{{Keywords:}} {_sanitize_latex(keywords)}\n\n"
    
    # Main content sections
    if structure.get("sections"):
        for section in structure["sections"]:
            content += _generate_section_latex(section, structure)
            content += "\n"
    else:
        # If no sections detected, output paragraphs
        if structure.get("paragraphs"):
            content += "\\section{Content}\n\n"
            for para in structure["paragraphs"][:20]:
                content += f"{_sanitize_latex(para)}\n\n"
    
    # References if present
    if structure.get("references"):
        content += "\\section{References}\n\n"
        content += "\\begin{thebibliography}{99}\n\n"
        for i, ref in enumerate(structure["references"], 1):
            content += f"\\bibitem{{{i}}} {_sanitize_latex(ref)}\n\n"
        content += "\\end{thebibliography}\n\n"
    
    # Closing
    content += "\\end{document}\n"
    
    return content


def _generate_preamble() -> str:
    """Generate LaTeX document preamble with necessary packages."""
    return r"""\documentclass{article}

\usepackage[utf-8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsfonts}
\usepackage{graphicx}
\usepackage{hyperref}
\usepackage{geometry}
\usepackage{booktabs}
\usepackage{array}
\usepackage{enumitem}
\usepackage{xcolor}
\usepackage{listings}
\usepackage{caption}
\usepackage{subcaption}
\usepackage{float}

\geometry{margin=1in}
\setlist{nosep}

"""


def _generate_section_latex(section: dict[str, Any], structure: dict[str, Any]) -> str:
    """Generate LaTeX for a single section."""
    
    title = _sanitize_latex(section.get("title", "Untitled"))
    level = section.get("level", 1)
    content = section.get("content", "")
    
    # Determine section command based on level
    if level == 0:
        cmd = "part"
    elif level == 1:
        cmd = "section"
    elif level == 2:
        cmd = "subsection"
    else:
        cmd = "subsubsection"
    
    latex = f"\\{cmd}{{{title}}}\n\n"
    
    # Add section content
    if content:
        latex += f"{_sanitize_latex(content)}\n\n"
    
    # Add any equations for this section (simplified association)
    # In a real implementation, would track which equations belong to which section
    
    # Add lists if present
    for lst in structure.get("lists", [])[:3]:
        latex += _generate_list_latex(lst) + "\n"
    
    # Add tables if present
    for table in structure.get("tables", [])[:3]:
        latex += _generate_table_latex(table) + "\n"
    
    return latex


def _generate_list_latex(lst: dict[str, Any]) -> str:
    """Generate LaTeX for a list."""
    list_type = lst.get("type", "bullet")
    items = lst.get("items", [])
    
    if not items:
        return ""
    
    if list_type == "numbered":
        env = "enumerate"
    else:
        env = "itemize"
    
    latex = f"\\begin{{{env}}}\n"
    for item in items[:50]:  # Limit to 50 items
        item_text = _sanitize_latex(item)
        latex += f"  \\item {item_text}\n"
    latex += f"\\end{{{env}}}\n"
    
    return latex


def _generate_table_latex(table: dict[str, Any]) -> str:
    """Generate LaTeX for a table."""
    rows = table.get("rows", [])
    cols = table.get("cols", 0)
    
    if not rows or cols < 1:
        return ""
    
    # Build table
    latex = "\\begin{table}[h]\n"
    latex += "\\centering\n"
    
    # Column spec (all columns centered)
    col_spec = " | ".join(["c"] * cols)
    latex += f"\\begin{{tabular}}{{{col_spec}}}\n"
    latex += "\\toprule\n"
    
    # Add rows
    for i, row in enumerate(rows[:20]):  # Limit to 20 rows
        # Ensure row has correct number of cells
        cells = row + [""] * max(0, cols - len(row))
        row_text = " & ".join(_sanitize_latex(str(cell)) for cell in cells[:cols])
        latex += row_text + " \\\\\n"
        
        # Add midrule after first row (header)
        if i == 0 and len(rows) > 1:
            latex += "\\midrule\n"
    
    latex += "\\bottomrule\n"
    latex += "\\end{tabular}\n"
    latex += "\\end{table}\n\n"
    
    return latex


def _sanitize_latex(text: str) -> str:
    """
    Escape text for safe inclusion in LaTeX while preserving readability.
    """
    if not text:
        return ""
    
    # Normalize whitespace
    text = " ".join(text.split())
    
    # Escape special LaTeX characters in specific order
    # First, protect already-valid LaTeX commands by replacing \command temporarily
    protected_commands = []
    def protect(match):
        protected_commands.append(match.group(0))
        return f"{{{{PROTECTED_{len(protected_commands)-1}}}}}"
    
    # Protect common LaTeX commands and math
    text = re.sub(r"\\[a-zA-Z]+\{[^}]*\}", protect, text)
    text = re.sub(r"\$[^\$]*\$", protect, text)
    
    # Now escape raw special characters
    replacements = {
        "\\": "\\textbackslash{}",  # Must come first
        "{": "\\{",
        "}": "\\}",
        "$": "\\$",
        "&": "\\&",
        "%": "\\%",
        "#": "\\#",
        "_": "\\_",
        "^": "\\textasciicircum{}",
        "~": "\\textasciitilde{}",
        "<": "\\textless{}",
        ">": "\\textgreater{}",
    }
    
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    
    # Restore protected commands
    for i, cmd in enumerate(protected_commands):
        text = text.replace(f"{{{{PROTECTED_{i}}}}}", cmd)
    
    # Limit length for safety
    return text[:5000]


# Add missing import
import re
