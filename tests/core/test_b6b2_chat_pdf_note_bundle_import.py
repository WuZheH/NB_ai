from pathlib import Path

from app.services.chat_import_catalog_service import note_sources
import pytest


def test_note_bundle_metadata_is_relative_and_hashed(tmp_path: Path):
    root = tmp_path / "inbox"
    root.mkdir()
    pdf = root / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    note = root / "paper.notes.md"
    note.write_text("note", encoding="utf-8")
    sources = note_sources(pdf=pdf, inbox_root=root)
    assert sources[0]["relative_path"] == "paper.notes.md"
    assert len(sources[0]["sha256"]) == 64
    assert "path" not in sources[0]

@pytest.mark.parametrize("mutation", ["add", "delete", "modify"])
def test_note_bundle_set_changes_are_detectable(tmp_path: Path, mutation: str):
    root = tmp_path / "inbox"; root.mkdir(); pdf = root / "paper.pdf"; pdf.write_bytes(b"%PDF")
    note = root / "paper.notes.md"; note.write_text("one", encoding="utf-8")
    before = tuple(note_sources(pdf=pdf, inbox_root=root))
    if mutation == "add": (root / "paper.notes" ).mkdir(); (root / "paper.notes" / "two.md").write_text("two", encoding="utf-8")
    elif mutation == "delete": note.unlink()
    else: note.write_text("changed", encoding="utf-8")
    assert tuple(note_sources(pdf=pdf, inbox_root=root)) != before
