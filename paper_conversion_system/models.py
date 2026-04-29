from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
import hashlib
import json
import time
import uuid


SUPPORTED_DIRECTIONS = {
    ("ieee", "acm"): "ieee_to_acm",
    ("acm", "ieee"): "acm_to_ieee",
}


@dataclass
class Section:
    title: str
    content: str


@dataclass
class Figure:
    label: str
    caption: str
    path: str
    placement: str = "tbp"


@dataclass
class Table:
    label: str
    caption: str
    latex: str
    placement: str = "tbp"


@dataclass
class Reference:
    key: str
    raw: str


@dataclass
class CanonicalPaperRepresentation:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    equations: list[str] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConversionReport:
    mapped_fields: list[str] = field(default_factory=list)
    changed_sections: list[str] = field(default_factory=list)
    unresolved_items: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    target_template_profile: str = ""
    source_format: str = ""
    target_format: str = ""
    ingest_confidence: float | None = None


@dataclass
class ValidationSummary:
    template_compliance: str = "unknown"
    citation_compliance: str = "unknown"
    compile_status: str = "not_run"
    fidelity_score: float | None = None
    fidelity_details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class CompileArtifacts:
    pdf_path: str | None = None
    log_path: str | None = None
    aux_path: str | None = None
    bbl_path: str | None = None


@dataclass
class JobOutput:
    job_id: str
    direction: str
    status: str
    converted_source_path: str | None
    final_pdf_path: str | None
    validation: ValidationSummary
    reports: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "direction": self.direction,
            "status": self.status,
            "converted_source_path": self.converted_source_path,
            "final_pdf_path": self.final_pdf_path,
            "validation": asdict(self.validation),
            "reports": self.reports,
        }


@dataclass
class StructuredLogEvent:
    step: str
    ts: float
    input_fingerprint: str
    mapping_operations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    compile_command_sequence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StructuredLogger:
    def __init__(self) -> None:
        self.events: list[StructuredLogEvent] = []

    def add(self, event: StructuredLogEvent) -> None:
        self.events.append(event)

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([e.to_dict() for e in self.events], indent=2), encoding="utf-8")


def fingerprint_path(path: Path) -> str:
    sha = hashlib.sha256()
    if path.is_file():
        sha.update(path.read_bytes())
    elif path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file():
                sha.update(str(child.relative_to(path)).encode("utf-8", errors="ignore"))
                sha.update(child.read_bytes())
    return sha.hexdigest()


def new_job_id() -> str:
    return f"job-{int(time.time())}-{uuid.uuid4().hex[:8]}"
