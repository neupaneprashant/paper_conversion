"""PDF to LaTeX conversion utilities with OCR and structure analysis."""

from __future__ import annotations

from pathlib import Path
import re
import json
import tempfile
import subprocess
import sys


def extract_text_from_pdf(pdf_path: Path) -> tuple[str, dict]:
    """
    Extract text from PDF using multiple methods with OCR fallback.
    
    Returns:
        Tuple of (extracted_text, metadata)
    """
    metadata = {
        "method": None,
        "pages": 0,
        "confidence": 0.0,
        "errors": []
    }
    
    # Try pdfplumber first (best for text-based PDFs)
    text = _extract_pdfplumber(pdf_path, metadata)
    if text and len(text.strip()) > 100:
        metadata["method"] = "pdfplumber"
        metadata["confidence"] = 0.9
        return text, metadata
    
    # Try pypdf
    text = _extract_pypdf(pdf_path, metadata)
    if text and len(text.strip()) > 100:
        metadata["method"] = "pypdf"
        metadata["confidence"] = 0.85
        return text, metadata
    
    # Try fitz (PyMuPDF)
    text = _extract_fitz(pdf_path, metadata)
    if text and len(text.strip()) > 100:
        metadata["method"] = "fitz"
        metadata["confidence"] = 0.8
        return text, metadata
    
    # Fallback to OCR (works for scanned PDFs)
    text = _extract_ocr(pdf_path, metadata)
    if text and len(text.strip()) > 100:
        metadata["method"] = "ocr-tesseract"
        metadata["confidence"] = 0.6
        return text, metadata
    
    metadata["errors"].append("All extraction methods failed")
    return "", metadata


def _extract_pdfplumber(pdf_path: Path, metadata: dict) -> str:
    """Extract text using pdfplumber."""
    try:
        import pdfplumber
        text = ""
        with pdfplumber.open(str(pdf_path)) as pdf:
            metadata["pages"] = len(pdf.pages)
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n\n"
        return text
    except Exception as e:
        metadata["errors"].append(f"pdfplumber failed: {e}")
        return ""


def _extract_pypdf(pdf_path: Path, metadata: dict) -> str:
    """Extract text using pypdf."""
    try:
        import pypdf
        reader = pypdf.PdfReader(str(pdf_path))
        metadata["pages"] = len(reader.pages)
        text = ""
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n\n"
        return text
    except Exception as e:
        metadata["errors"].append(f"pypdf failed: {e}")
        return ""


def _extract_fitz(pdf_path: Path, metadata: dict) -> str:
    """Extract text using PyMuPDF (fitz)."""
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        metadata["pages"] = doc.page_count
        text = ""
        for page_num in range(doc.page_count):
            page = doc[page_num]
            text += page.get_text() + "\n\n"
        doc.close()
        return text
    except Exception as e:
        metadata["errors"].append(f"fitz failed: {e}")
        return ""


def _extract_ocr(pdf_path: Path, metadata: dict) -> str:
    """Extract text using OCR (Tesseract) via PDF rasterization."""
    try:
        import fitz
        from PIL import Image
        import pytesseract
        
        # Rasterize PDF pages to images
        doc = fitz.open(str(pdf_path))
        metadata["pages"] = doc.page_count
        text = ""
        
        for page_num in range(doc.page_count):
            page = doc[page_num]
            # Render page to image
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better OCR
            
            # Save to temp file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                pix.save(tmp.name)
                
                # OCR the image
                try:
                    img = Image.open(tmp.name)
                    page_text = pytesseract.image_to_string(img)
                    text += page_text + "\n\n"
                finally:
                    Path(tmp.name).unlink()
        
        doc.close()
        return text
        
    except Exception as e:
        metadata["errors"].append(f"OCR failed: {e}")
        return ""


def pdf_to_latex_project(pdf_path: Path, output_dir: Path) -> Path:
    """
    Convert a PDF paper to a LaTeX project structure using full pipeline.
    
    Args:
        pdf_path: Path to PDF file
        output_dir: Directory to write LaTeX project files
        
    Returns:
        Path to generated main.tex file
    """
    from .structure_analyzer import analyze_text_structure
    from .latex_generator import generate_latex_from_structure
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Stage 1: Extract text from PDF
    print(f"[PDF Parser] Extracting text from {pdf_path.name}...")
    text, extract_meta = extract_text_from_pdf(pdf_path)
    print(f"[OK] Extracted {len(text.strip())} chars using {extract_meta['method']} "
          f"(confidence: {extract_meta['confidence']:.0%})")
    
    if not text or len(text.strip()) < 50:
        print("! Could not extract sufficient text, creating minimal document")
        text = f"[Content extracted from: {pdf_path.name}]"
    
    # Stage 2: Analyze structure
    print("[Structure Analyzer] Analyzing document structure...")
    structure = analyze_text_structure(text)
    print(f"[OK] Detected {len(structure.get('sections', []))} sections, "
          f"{len(structure.get('equations', []))} equations, "
          f"{len(structure.get('tables', []))} tables")
    
    # Stage 3: Generate LaTeX
    print("[LaTeX Generator] Generating LaTeX from structure...")
    main_tex = generate_latex_from_structure(structure, pdf_path.stem)
    
    # Write files
    main_tex_path = output_dir / "main.tex"
    main_tex_path.write_text(main_tex, encoding="utf-8")
    print(f"[OK] Generated {main_tex_path}")
    
    bib_path = output_dir / "references.bib"
    bib_path.write_text(_generate_placeholder_bib(), encoding="utf-8")
    
    # Save metadata
    meta_path = output_dir / "extraction_metadata.json"
    meta_path.write_text(json.dumps(extract_meta, indent=2), encoding="utf-8")
    
    return main_tex_path


def _generate_placeholder_bib() -> str:
    """Generate a placeholder BibTeX file."""
    return """@article{unknown,
  title={Unknown},
  author={Unknown},
  year={2024},
  journal={Unknown}
}
"""
