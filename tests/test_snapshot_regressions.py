from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_conversion_system.agents import April, Friday


ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = ROOT / "tests" / "fixtures"
SNAPSHOTS_ROOT = ROOT / "tests" / "snapshots"
MANIFEST_PATH = FIXTURES_ROOT / "manifest.json"


def _load_manifest() -> list[dict]:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list), "Fixture manifest must be a JSON list"
    return data


def _normalise_text(text: str) -> str:
    return text.replace("\r\n", "\n").strip() + "\n"


@pytest.mark.parametrize("case", _load_manifest(), ids=lambda c: c["id"])
def test_conversion_snapshots(case: dict, tmp_path: Path, request: pytest.FixtureRequest) -> None:
    update = bool(request.config.getoption("--update-snapshots"))

    fixture_dir = FIXTURES_ROOT / case["input_dir"]
    assert fixture_dir.exists(), f"Missing fixture directory: {fixture_dir}"

    source = case["source_format"].lower()
    target = case["target_format"].lower()
    if source == "ieee" and target == "acm":
        agent = April()
    elif source == "acm" and target == "ieee":
        agent = Friday()
    else:
        raise AssertionError(f"Unsupported fixture direction: {source} -> {target}")

    converted_dir = tmp_path / case["id"] / "converted"
    converted_dir.mkdir(parents=True, exist_ok=True)
    project_dir, _, _ = agent.convert(fixture_dir, converted_dir)
    assert project_dir == converted_dir

    snapshot_dir = SNAPSHOTS_ROOT / case["id"]
    files = ["main.tex", "references.bib"]

    for name in files:
        actual_path = converted_dir / name
        assert actual_path.exists(), f"Missing converted artifact: {actual_path}"
        actual_text = _normalise_text(actual_path.read_text(encoding="utf-8", errors="ignore"))

        expected_path = snapshot_dir / name
        if update or not expected_path.exists():
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected_path.write_text(actual_text, encoding="utf-8")
            continue

        expected_text = _normalise_text(expected_path.read_text(encoding="utf-8", errors="ignore"))
        assert actual_text == expected_text, (
            f"Snapshot mismatch for {case['id']}/{name}. "
            "Run pytest with --update-snapshots to accept intentional changes."
        )


def test_snapshot_manifest_size() -> None:
    manifest = _load_manifest()
    assert len(manifest) >= 5, "Add at least 5 fixture cases to catch regressions reliably."

