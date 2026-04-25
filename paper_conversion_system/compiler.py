from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import shutil
import subprocess

from .models import CompileArtifacts


def compile_project(project_dir: Path, max_repairs: int = 2) -> tuple[str, CompileArtifacts, dict]:
    """Compile a rendered LaTeX project with limited automated repair attempts.

    Returns a status string, compile artifacts, and a structured compile report.
    If pdflatex is unavailable, the function reports a structured failure rather
    than throwing, so higher-level orchestration can still return actionable output.
    """
    commands_run: list[str] = []
    repair_notes: list[str] = []
    log_snippets: list[str] = []

    pdflatex = shutil.which("pdflatex")
    bibtex = shutil.which("bibtex")

    if not pdflatex:
        return (
            "failed",
            CompileArtifacts(),
            {
                "commands": commands_run,
                "repairs": repair_notes,
                "log_snippets": ["pdflatex not found on PATH"],
                "suggested_fixes": ["Install a LaTeX distribution with pdflatex and bibtex"],
            },
        )

    main = project_dir / "main.tex"
    for attempt in range(max_repairs + 1):
        status, snippet, cmds = _run_compile_cycle(project_dir, main, pdflatex, bibtex)
        commands_run.extend(cmds)
        if snippet:
            log_snippets.append(snippet)
        if status == "success":
            return (
                "success",
                CompileArtifacts(
                    pdf_path=str(project_dir / "main.pdf") if (project_dir / "main.pdf").exists() else None,
                    log_path=str(project_dir / "main.log") if (project_dir / "main.log").exists() else None,
                    aux_path=str(project_dir / "main.aux") if (project_dir / "main.aux").exists() else None,
                    bbl_path=str(project_dir / "main.bbl") if (project_dir / "main.bbl").exists() else None,
                ),
                {
                    "commands": commands_run,
                    "repairs": repair_notes,
                    "log_snippets": log_snippets,
                    "suggested_fixes": [],
                },
            )

        if attempt < max_repairs:
            repair_notes.append(_attempt_repair(project_dir, attempt))

    return (
        "failed",
        CompileArtifacts(
            pdf_path=str(project_dir / "main.pdf") if (project_dir / "main.pdf").exists() else None,
            log_path=str(project_dir / "main.log") if (project_dir / "main.log").exists() else None,
            aux_path=str(project_dir / "main.aux") if (project_dir / "main.aux").exists() else None,
            bbl_path=str(project_dir / "main.bbl") if (project_dir / "main.bbl").exists() else None,
        ),
        {
            "commands": commands_run,
            "repairs": repair_notes,
            "log_snippets": log_snippets,
            "suggested_fixes": [
                "Inspect missing packages in main.log",
                "Confirm bibliography file exists and matches \\bibliography{references}",
                "Review unresolved LaTeX syntax around reported line numbers",
            ],
        },
    )


_BIBTEX_SOFT_FAILURES = (
    "I found no \\citation commands",
    "I found no \\bibdata command",
    "I found no \\bibstyle command",
    "So far, you have not checked for MiKTeX updates",
)

# pdflatex returncode can be non-zero even when the PDF was produced. This
# happens with MiKTeX's "major issue: So far, you have not checked for MiKTeX
# updates" warning, plus a few benign package warnings that some distros
# promote to errors. We treat these as soft signals only.
_PDFLATEX_SOFT_RETURN_MARKERS = (
    "Output written on main.pdf",
    "So far, you have not checked for MiKTeX updates",
)


def _pdflatex_succeeded(project_dir: Path, proc_output: str) -> bool:
    """Return True if pdflatex actually produced a PDF regardless of exit code.

    Some LaTeX distributions (notably MiKTeX) exit non-zero for warnings that
    don't prevent a valid PDF from being written. We consider a pass to have
    occurred when main.pdf exists AND the stdout/stderr indicates a PDF was
    written for the current run.
    """
    pdf_ok = (project_dir / "main.pdf").exists()
    wrote_pdf = any(marker in proc_output for marker in _PDFLATEX_SOFT_RETURN_MARKERS)
    return pdf_ok and wrote_pdf


def _run_compile_cycle(project_dir: Path, main: Path, pdflatex: str, bibtex: str | None) -> tuple[str, str, list[str]]:
    cmds: list[str] = []
    snippet = ""
    main_text = main.read_text(encoding="utf-8", errors="ignore") if main.exists() else ""
    needs_bibtex = "\\bibliography{" in main_text or "\\bibliographystyle{" in main_text

    first = [pdflatex, "-interaction=nonstopmode", main.name]
    cmds.append(" ".join(first))
    proc1 = subprocess.run(first, cwd=project_dir, capture_output=True, text=True, errors="replace")
    proc1_output = (proc1.stdout or "") + "\n" + (proc1.stderr or "")
    if proc1.returncode != 0 and not _pdflatex_succeeded(project_dir, proc1_output):
        snippet = _tail(proc1_output)
        return "failed", snippet, cmds

    if needs_bibtex and bibtex and (project_dir / "main.aux").exists():
        bib = [bibtex, "main"]
        cmds.append(" ".join(bib))
        proc_bib = subprocess.run(bib, cwd=project_dir, capture_output=True, text=True, errors="replace")
        bib_output = (proc_bib.stdout or "") + "\n" + (proc_bib.stderr or "")
        bibtex_is_soft = any(marker in bib_output for marker in _BIBTEX_SOFT_FAILURES)
        if proc_bib.returncode != 0 and not bibtex_is_soft:
            snippet = _tail(bib_output)
            return "failed", snippet, cmds

    second = [pdflatex, "-interaction=nonstopmode", main.name]
    third = [pdflatex, "-interaction=nonstopmode", main.name]
    for cmd in (second, third):
        cmds.append(" ".join(cmd))
        proc = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True, errors="replace")
        proc_output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if proc.returncode != 0 and not _pdflatex_succeeded(project_dir, proc_output):
            snippet = _tail(proc_output)
            return "failed", snippet, cmds

    # Only confirm success if we actually produced a PDF.
    if not (project_dir / "main.pdf").exists():
        return "failed", "pdflatex returned 0 but no main.pdf was produced", cmds

    return "success", "", cmds


def _attempt_repair(project_dir: Path, attempt: int) -> str:
    main = project_dir / "main.tex"
    if not main.exists():
        return "No repair possible: main.tex missing"
    original = main.read_text(encoding="utf-8", errors="ignore")
    text = original
    repairs: list[str] = []

    # Always safe: remove any duplicate \end{document} tails that sometimes slip
    # in when a section body was captured without a terminator.
    end_doc_count = text.count("\\end{document}")
    if end_doc_count > 1:
        first_idx = text.find("\\end{document}")
        text = text[: first_idx + len("\\end{document}")] + "\n"
        repairs.append(f"removed {end_doc_count - 1} duplicate \\end{{document}}")

    # Remove duplicate \bibliography and \bibliographystyle lines (keep last occurrence).
    for cmd in ("\\bibliographystyle", "\\bibliography{references}", "\\begin{thebibliography}"):
        occurrences = text.count(cmd)
        if occurrences > 1:
            # Keep only the final occurrence by blanking earlier ones.
            new_text = text
            for _ in range(occurrences - 1):
                idx = new_text.find(cmd)
                line_end = new_text.find("\n", idx)
                if idx == -1 or line_end == -1:
                    break
                new_text = new_text[:idx] + new_text[line_end + 1:]
            text = new_text
            repairs.append(f"collapsed duplicate {cmd}")

    if attempt == 0 and "\\usepackage{graphicx}" not in text:
        text = text.replace("\\begin{document}", "\\usepackage{graphicx}\n\\begin{document}", 1)
        repairs.append("inserted missing graphicx import")

    if attempt >= 1 and "\\bibliography{references}" not in text and "\\begin{thebibliography}" not in text:
        text = text.replace("\\end{document}", "\\bibliography{references}\n\\end{document}", 1)
        repairs.append("inserted missing \\bibliography command")

    if attempt >= 1 and "\\bibliographystyle" not in text and "\\bibliography{references}" in text:
        # Decide a sensible default style based on document class.
        style = "ACM-Reference-Format" if "{acmart}" in text else "IEEEtran"
        text = text.replace(
            "\\bibliography{references}",
            f"\\bibliographystyle{{{style}}}\n\\bibliography{{references}}",
            1,
        )
        repairs.append(f"inserted missing \\bibliographystyle{{{style}}}")

    if text != original:
        main.write_text(text, encoding="utf-8")
        return "; ".join(repairs) if repairs else "normalized main.tex"
    return "no repair applied"


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.splitlines()[-lines:])
