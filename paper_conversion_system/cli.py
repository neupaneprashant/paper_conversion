from __future__ import annotations

import argparse
import json
from pathlib import Path

from .orchestrator import route_and_run
from .pdf_parser import pdf_to_latex_project


def main() -> None:
    parser = argparse.ArgumentParser(description="3-Agent Paper Conversion System")
    sub = parser.add_subparsers(dest="command", required=True)

    # Original convert command (LaTeX ↔ LaTeX)
    convert = sub.add_parser("convert")
    convert.add_argument("--source-format", required=True, choices=["ieee", "acm"])
    convert.add_argument("--target-format", required=True, choices=["ieee", "acm"])
    convert.add_argument("--input", required=True)
    convert.add_argument("--workdir", required=True)

    # New PDF to LaTeX command
    pdf2latex = sub.add_parser("pdf2latex")
    pdf2latex.add_argument("--input", type=Path, required=True, help="Path to input PDF file")
    pdf2latex.add_argument("--output", type=Path, required=True, help="Directory for output LaTeX project")

    args = parser.parse_args()

    if args.command == "convert":
        result = route_and_run(
            source_format=args.source_format,
            target_format=args.target_format,
            input_path=Path(args.input),
            workdir=Path(args.workdir),
        )
        print(json.dumps(result.to_dict(), indent=2))
    
    elif args.command == "pdf2latex":
        try:
            main_tex_path = pdf_to_latex_project(args.input, args.output)
            result = {
                "success": True,
                "message": f"PDF converted successfully",
                "input_file": str(args.input),
                "output_dir": str(args.output),
                "main_tex": str(main_tex_path),
                "metadata_file": str(args.output / "extraction_metadata.json")
            }
            print(json.dumps(result, indent=2))
        except Exception as e:
            result = {
                "success": False,
                "error": str(e),
                "input_file": str(args.input)
            }
            print(json.dumps(result, indent=2))
            exit(1)


if __name__ == "__main__":
    main()
