from pathlib import Path

from paper_conversion_system.api import (
    ROOT,
    _browse_shortcuts,
    _build_browse_payload,
    _resolve_browse_target,
)


def test_browse_default_path_prefers_user_space():
    target = _resolve_browse_target("")
    candidates = [
        candidate.resolve()
        for candidate in (Path.home() / "Desktop", Path.home() / "Downloads", Path.home())
        if candidate.exists() and candidate.is_dir()
    ]
    assert target in candidates
    assert target != ROOT.resolve()


def test_browse_relative_path_still_resolves_against_workspace():
    target = _resolve_browse_target("./samples/acm_sample")
    assert target == (ROOT / "samples" / "acm_sample").resolve()


def test_browse_file_path_uses_parent_directory(tmp_path: Path):
    file_path = tmp_path / "paper.pdf"
    file_path.write_text("demo", encoding="utf-8")
    target = _resolve_browse_target(str(file_path))
    assert target == tmp_path.resolve()


def test_browse_payload_exposes_shortcuts_and_items(tmp_path: Path):
    child = tmp_path / "child"
    child.mkdir()
    file_path = tmp_path / "notes.txt"
    file_path.write_text("demo", encoding="utf-8")

    payload = _build_browse_payload(tmp_path.resolve())

    assert payload["current_path"] == str(tmp_path.resolve())
    assert payload["parent_path"] == str(tmp_path.resolve().parent)
    assert any(item["name"] == "child" and item["is_dir"] for item in payload["items"])
    assert any(item["name"] == "notes.txt" and item["is_file"] for item in payload["items"])
    labels = {item["label"] for item in payload["shortcuts"]}
    assert "Home" in labels
    assert "Workspace" in labels


def test_browse_shortcuts_are_unique():
    shortcuts = _browse_shortcuts()
    paths = [item["path"] for item in shortcuts]
    assert len(paths) == len(set(paths))
