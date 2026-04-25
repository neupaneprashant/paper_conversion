from __future__ import annotations

from pathlib import Path

import fitz


def render_pdf_pages(pdf_path: Path, output_dir: Path, prefix: str = "page") -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[str] = []
    document = fitz.open(str(pdf_path))
    try:
        for page_number, page in enumerate(document, start=1):
            png_path = output_dir / f"{prefix}-{page_number}.png"
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pixmap.save(png_path)
            rendered.append(str(png_path))
    finally:
        document.close()
    return rendered
