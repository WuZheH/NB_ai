from pathlib import Path

from app.services.chat_import_catalog_service import list_catalog


def test_catalog_recursive_and_deterministic_sidecars(tmp_path: Path):
    root = tmp_path / "inbox"
    (root / "Deep Learning" / "notes").mkdir(parents=True)
    (root / "Deep Learning" / "Deep Learning.pdf").write_bytes(b"%PDF")
    (root / "Deep Learning" / "notes" / "optimization.md").write_text("# Optimization\nMomentum", encoding="utf-8")
    result = list_catalog(inbox_root=root)
    assert result["count"] == 1
    assert result["items"][0]["import_ref"] == "Deep Learning/Deep Learning.pdf"
    assert result["items"][0]["note_count"] == 1
    assert not any("\\" in value for value in result["items"][0]["note_files"])


def test_catalog_does_not_guess_notes_in_multi_pdf_folder(tmp_path: Path):
    root = tmp_path / "inbox"
    root.mkdir()
    (root / "a.pdf").write_bytes(b"%PDF")
    (root / "b.pdf").write_bytes(b"%PDF")
    (root / "notes.md").write_text("unrelated", encoding="utf-8")
    result = list_catalog(inbox_root=root)
    assert all(item["note_count"] == 0 for item in result["items"])
