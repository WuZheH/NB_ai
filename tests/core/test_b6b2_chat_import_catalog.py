from pathlib import Path

from app.services.chat_import_catalog_service import list_catalog
import pytest


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

@pytest.mark.parametrize("name", ["a.pdf", "nested/b.pdf", "x/y/z.pdf", "paper.PDF"])
def test_catalog_lists_pdf_entries(tmp_path: Path, name: str):
    root = tmp_path / "inbox"; path = root / name; path.parent.mkdir(parents=True); path.write_bytes(b"%PDF")
    result = list_catalog(inbox_root=root)
    assert result["count"] == 1 and result["items"][0]["status"] == "available"

def test_catalog_limit_is_enforced(tmp_path: Path):
    root = tmp_path / "inbox"; root.mkdir()
    for i in range(5): (root / f"{i}.pdf").write_bytes(b"%PDF")
    assert list_catalog(inbox_root=root, limit=2)["count"] == 2

def test_catalog_query_filters_title(tmp_path: Path):
    root = tmp_path / "inbox"; root.mkdir(); (root / "motion.pdf").write_bytes(b"%PDF")
    assert list_catalog(inbox_root=root, query="motion")["count"] == 1
    assert list_catalog(inbox_root=root, query="other")["count"] == 0
