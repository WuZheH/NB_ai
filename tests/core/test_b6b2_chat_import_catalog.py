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

@pytest.mark.parametrize(
    "name",
    [
        "a.pdf",
        "nested/b.pdf",
        "x/y/z.pdf",
        "paper.PDF",
        "paper.PdF",
        "nested/UPPER.PDF",
    ],
)
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


def test_catalog_excludes_non_pdf_and_symlink_like_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"%PDF")
    (root / "not-pdf.txt").write_text("not pdf", encoding="utf-8")
    link = root / "link.PDF"
    link.write_bytes(b"%PDF link stand-in")
    original_is_symlink = Path.is_symlink

    def fake_is_symlink(path: Path) -> bool:
        return path == link or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)

    result = list_catalog(inbox_root=root)

    assert [item["file_name"] for item in result["items"]] == ["paper.pdf"]


def test_catalog_excludes_resolved_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    escaping = root / "escape.pdf"
    escaping.write_bytes(b"%PDF")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF outside")
    original_resolve = Path.resolve

    def fake_resolve(path: Path, strict: bool = False) -> Path:
        if path == escaping:
            return outside
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    assert list_catalog(inbox_root=root)["count"] == 0


def test_catalog_order_is_casefold_deterministic_and_deduplicated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    upper = root / "B.PDF"
    lower = root / "a.pdf"
    upper.write_bytes(b"%PDF upper")
    lower.write_bytes(b"%PDF lower")
    original_rglob = Path.rglob

    def duplicate_rglob(path: Path, pattern: str):
        values = list(original_rglob(path, pattern))
        return iter([*values, *values])

    monkeypatch.setattr(Path, "rglob", duplicate_rglob)

    result = list_catalog(inbox_root=root)

    assert [item["file_name"] for item in result["items"]] == ["a.pdf", "B.PDF"]
