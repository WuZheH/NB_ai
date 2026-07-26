from pathlib import Path

from app.services.chat_import_catalog_service import note_sources


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
