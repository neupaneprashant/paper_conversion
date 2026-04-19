"""Document structure analysis for PDF-extracted text."""

from __future__ import annotations

import re
from typing import Any, Optional


def analyze_text_structure(text: str) -> dict[str, Any]:
    """
    Analyze extracted text to identify document structure.

    This module is broader and more exploratory than the CPR-specific PDF ingest
    path. It is useful for debugging, direct PDF-to-LaTeX experiments, and general
    structure inspection where we want a structural summary rather than strict
    venue-conversion behavior.
    
    Detects:
    - Title/metadata
    - Abstract
    - Sections and subsections
    - Paragraphs
    - Mathematical equations
    - Tables  
    - Lists
    - References
    
    Returns:
        Dictionary with 'title', 'abstract', 'sections', 'equations', 'tables', etc.
    """
    
    structure = {
        "title": None,
        "abstract": None,
        "authors": [],
        "keywords": [],
        "sections": [],
        "equations": [],
        "tables": [],
        "lists": [],
        "references": [],
        "paragraphs": []
    }
    
    lines = text.split('\n')
    
    # Extract title (usually first substantial line)
    structure["title"] = _extract_title(lines)
    
    # Extract abstract
    abstract_block = _extract_block(text, r"(?:abstract|summary)", 300)
    if abstract_block:
        structure["abstract"] = abstract_block
    
    # Extract author information if present
    authors = _extract_authors(text)
    if authors:
        structure["authors"] = authors
    
    # Extract keywords if present
    keywords = _extract_keywords(text)
    if keywords:
        structure["keywords"] = keywords
    
    # Remove already-extracted metadata from text for further analysis
    content_text = _remove_metadata_sections(text)
    
    # Extract equations (delimited by $ ... $ or $$ ... $$)
    structure["equations"] = _extract_equations(content_text)
    
    # Extract tables (lines with multiple | or tab-separated content)
    structure["tables"] = _extract_tables(content_text)
    
    # Extract lists (lines starting with -, *, or digits followed by .)
    structure["lists"] = _extract_lists(content_text)
    
    # Extract sections (lines that appear to be headings)
    structure["sections"] = _extract_sections(content_text)
    
    # Extract references (numbered citations or author-year)
    structure["references"] = _extract_references(content_text)
    
    # Extract remaining paragraphs
    structure["paragraphs"] = _extract_paragraphs(content_text)
    
    return structure


def _extract_title(lines: list[str]) -> Optional[str]:
    """Extract document title from first few lines."""
    for line in lines[:20]:
        line = line.strip()
        # Skip short lines and common headers
        if len(line) > 10 and len(line) < 200:
            if not re.match(r"^(page|abstract|introduction|figure|table)", line, re.I):
                return line
    return None


def _extract_block(text: str, pattern: str, max_length: int = 500) -> Optional[str]:
    """Extract a text block matching pattern."""
    try:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            # Get text after the match
            start = match.start()
            # Find next section or end
            next_section = re.search(r"\n\s*(?:[0-9]+\.|[A-Z][A-Za-z\s]+[\:.])", text[start+len(pattern):])
            if next_section:
                end = start + len(pattern) + next_section.start()
            else:
                end = min(start + max_length, len(text))
            
            block = text[start:end].strip()
            # Remove the pattern itself from result
            block = re.sub(pattern, "", block, flags=re.IGNORECASE).strip()
            return block[:max_length] if block else None
    except Exception:
        pass
    return None


def _extract_authors(text: str) -> list[str]:
    """Extract author names if present."""
    # Try common patterns for author sections
    patterns = [
        r"(?:by|authors?|author.*?):\s*([^,\n]+(?:,\s*[^,\n]+)*)",
        r"([A-Z][a-z]+\s+[A-Z][a-z]+)(?:\s+and|\s*,|\s+\d|\s*$)",
    ]
    
    authors = []
    for pattern in patterns:
        matches = re.findall(pattern, text[:1000])
        if matches:
            # Clean and split matches
            for match in matches[:5]:  # Limit to 5 authors
                parts = re.split(r"\s+and\s+|,\s*", match)
                for author in parts:
                    author = author.strip()
                    if 3 < len(author) < 100:
                        authors.append(author)
            if authors:
                break
    
    return list(dict.fromkeys(authors))[:5]  # Remove duplicates, limit to 5


def _extract_keywords(text: str) -> list[str]:
    """Extract keywords if present."""
    match = re.search(
        r"(?:keywords?|topics?)[\s:]*([^\n]+)",
        text[:2000],
        re.IGNORECASE
    )
    if match:
        keywords_text = match.group(1)
        keywords = re.split(r"[,;]\s*", keywords_text)
        return [k.strip().lower() for k in keywords if 2 < len(k.strip()) < 50][:10]
    return []


def _remove_metadata_sections(text: str) -> str:
    """Remove abstract, author, keyword sections from text."""
    # Remove abstract sections
    text = re.sub(r"(?:abstract|summary)[\s:].*?(?=\n[0-9]|\nintroduction|$)", "", text, flags=re.IGNORECASE | re.DOTALL)
    return text


def _extract_equations(text: str) -> list[dict[str, str]]:
    """Extract mathematical equations."""
    equations = []
    
    # Find $...$ inline equations
    for match in re.finditer(r"\$([^\$]+)\$", text):
        equations.append({
            "type": "inline",
            "content": match.group(1).strip()
        })
    
    # Find $$...$$ display equations
    for match in re.finditer(r"\$\$([^\$\$]+)\$\$", text, re.DOTALL):
        equations.append({
            "type": "display",
            "content": match.group(1).strip()
        })
    
    # Find equation-like patterns (common in PDFs)
    # Pattern: (num) or [num] with math-like content
    for match in re.finditer(r"(?:\([\d.]+\)|\[[\d.]+\])\s*([=><∈∊∉∋⊂⊃⊆⊇∪∩×÷±∓∝∞∫∑∏√][^\n]+)", text):
        equations.append({
            "type": "equation",
            "content": match.group(1).strip()
        })
    
    return equations[:50]  # Limit to 50 equations


def _extract_tables(text: str) -> list[dict[str, Any]]:
    """Extract table structures."""
    tables = []
    
    lines = text.split('\n')
    current_table = []
    
    for line in lines:
        # Table line patterns: contains | (pipes) or multiple spaces between words
        if '|' in line or re.match(r"^[\s\w]+\s{2,}[\w\s]+", line):
            # Could be a table row
            if len(line.strip()) > 10:
                cells = re.split(r'\s{2,}|\|', line.strip())
                cells = [c.strip() for c in cells if c.strip()]
                if len(cells) > 1:
                    current_table.append(cells)
        elif current_table and len(line.strip()) < 3:
            # Empty line after table – save it
            if len(current_table) >= 2:
                tables.append({
                    "rows": current_table,
                    "cols": len(current_table[0]) if current_table else 0
                })
            current_table = []
    
    # Save last table if exists
    if len(current_table) >= 2:
        tables.append({
            "rows": current_table,
            "cols": len(current_table[0]) if current_table else 0
        })
    
    return tables[:20]  # Limit to 20 tables


def _extract_lists(text: str) -> list[dict[str, Any]]:
    """Extract list structures."""
    lists = []
    
    lines = text.split('\n')
    current_list = []
    list_type = None
    
    for line in lines:
        # Match bullet points, dashes, or numbered lists
        match = re.match(r"^\s*([•\-\*\+]|[0-9]+\.)\s+(.+)", line)
        if match:
            item_type = "numbered" if match.group(1)[0].isdigit() else "bullet"
            
            # If list type changed, save previous list
            if list_type and list_type != item_type:
                if current_list:
                    lists.append({"type": list_type, "items": current_list})
                current_list = []
            
            list_type = item_type
            current_list.append(match.group(2).strip())
        elif current_list and len(line.strip()) < 3:
            # Empty line – end list
            if current_list:
                lists.append({"type": list_type, "items": current_list})
            current_list = []
            list_type = None
    
    # Save last list
    if current_list:
        lists.append({"type": list_type, "items": current_list})
    
    return lists[:30]  # Limit to 30 lists


def _extract_sections(text: str) -> list[dict[str, Any]]:
    """Extract section headings and their content."""
    sections = []
    
    lines = text.split('\n')
    current_section = None
    current_content = []
    
    for i, line in enumerate(lines):
        # Detect heading patterns:
        # - ALL CAPS lines (but with minimum length)
        # - Lines followed by ===== or ------
        # - Lines starting with numbers (1. 2. etc.)
        # - Lines that appear to be standalone headings
        
        is_heading = False
        level = 0
        title = None
        
        # Pattern: numbered section (1, 1.1, 1.1.1, etc.)
        # Must be at start of line and followed by title
        match = re.match(r"^([0-9]+(?:\.[0-9]+)*)\s+([A-Z][^\n]+?)(?:\s*$|\s+\(|\.)", line.strip())
        if match and len(match.group(2)) > 3:
            is_heading = True
            level = 1 + line.count('.')
            title = f"{match.group(1)} {match.group(2)}"
        
        # Pattern: ALL CAPS (title-like, but minimum length and reasonable max)
        elif (not is_heading and 8 < len(line.strip()) < 150 and 
              line.strip().isupper() and 
              not line.strip().endswith(':') and
              line.count(' ') > 0):  # At least 2 words
            # Make sure it's not just an acronym or single word
            words = line.strip().split()
            if len(words) >= 2 and not all(w.isupper() and len(w) <= 3 for w in words):
                is_heading = True
                level = 1
                title = line.strip()
        
        # Pattern: underlined with === or --- (must be full line of === or ---)
        elif not is_heading and i + 1 < len(lines):
            next_line = lines[i + 1]
            if (len(line.strip()) > 3 and
                re.match(r"^[=\-]{5,}$", next_line.strip()) and
                7 < len(line.strip()) < 150):  # Reasonable title length
                is_heading = True
                level = 1 if '=' in next_line else 2
                title = line.strip()
        
        if is_heading and title:
            # Save previous section
            if current_section:
                current_section["content"] = '\n'.join(current_content).strip()
                sections.append(current_section)
            
            current_section = {
                "title": title,
                "level": level,
                "content": ""
            }
            current_content = []
        else:
            # Add to current section content
            if line.strip():
                current_content.append(line)
    
    # Save last section
    if current_section:
        current_section["content"] = '\n'.join(current_content).strip()
        sections.append(current_section)
    
    return sections


def _extract_references(text: str) -> list[str]:
    """Extract bibliographic references."""
    references = []
    
    # Pattern: [1] Author et al. ...
    for match in re.finditer(r"\[\d+\]\s+([^\n]+(?:\n[^\n]+)*?)(?:\n\s*\[|\Z)", text):
        ref = match.group(1).strip()
        if ref and len(ref) > 10:
            references.append(ref)
    
    # Pattern: Author et al. (Year)
    for match in re.finditer(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+et\s+al.\s*\([0-9]{4}\)[^\n]*)", text):
        ref = match.group(1).strip()
        if len(ref) > 10:
            references.append(ref)
    
    return references[:100]  # Limit to 100 references


def _extract_paragraphs(text: str) -> list[str]:
    """Extract body paragraphs (content not in other structures)."""
    paragraphs = []
    
    # Split by double newlines (paragraph breaks)
    para_candidates = text.split('\n\n')
    
    for para in para_candidates:
        para = para.strip()
        
        # Filter out very short text and already-extracted structures
        if 50 < len(para) < 3000:
            # Skip if looks like a heading or list
            if not re.match(r"^([0-9]+\.|[•\-\*]|\$|Table|Figure|Algorithm)", para, re.I):
                # Clean up excessive whitespace
                para = re.sub(r'\s+', ' ', para)
                paragraphs.append(para)
    
    return paragraphs[:100]  # Limit to 100 paragraphs
