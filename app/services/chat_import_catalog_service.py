from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

MAX_NOTE_FILES = 200
MAX_NOTE_BYTES = 20 * 1024 * 1024


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _notes_for_pdf(pdf: Path, root: Path) -> list[Path]:
    candidates: list[Path] = []
    stem = pdf.with_suffix("")
    for suffix in (".md", ".txt", ".notes.md", ".notes.txt"):
        p = Path(str(stem) + suffix)
        if p.is_file() and _inside(root, p.resolve(strict=False)):
            candidates.append(p)
    notes_dir = Path(str(stem) + ".notes")
    if notes_dir.is_dir():
        candidates.extend(p for p in notes_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".txt"})
    sibling_pdfs = [p for p in pdf.parent.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
    if len(sibling_pdfs) == 1:
        notes_dir = pdf.parent / "notes"
        if notes_dir.is_dir():
            candidates.extend(p for p in notes_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".txt"})
    unique = sorted({p.resolve(strict=False) for p in candidates if _inside(root, p.resolve(strict=False))}, key=lambda p: p.as_posix())
    total = 0
    result: list[Path] = []
    for path in unique[:MAX_NOTE_FILES]:
        size = path.stat().st_size
        if total + size > MAX_NOTE_BYTES:
            break
        total += size
        result.append(path)
    return result


def list_catalog(*, inbox_root: Path, query: str | None = None, limit: int = 50) -> dict[str, Any]:
    root = Path(inbox_root).resolve(strict=False)
    if not root.is_dir():
        return {"status": "ok", "scope": "catalog", "count": 0, "items": [], "truncated": False}
    needle = (query or "").strip().casefold()
    items: list[dict[str, Any]] = []
    for pdf in sorted(root.rglob("*.pdf"), key=lambda p: p.as_posix().casefold()):
        resolved = pdf.resolve(strict=False)
        if not _inside(root, resolved) or resolved.suffix.lower() != ".pdf" or not resolved.is_file():
            continue
        relative = resolved.relative_to(root).as_posix()
        title = resolved.stem
        if needle and needle not in title.casefold() and needle not in relative.casefold():
            continue
        notes = _notes_for_pdf(resolved, root)
        items.append({
            "kind": "catalog", "document_id": None, "title": title, "type": "pdf", "has_pdf": True,
            "import_ref": relative, "file_name": resolved.name, "relative_path": relative,
            "note_count": len(notes), "note_files": [p.relative_to(root).as_posix() for p in notes],
            "status": "available", "duplicate_status": "not_evaluated",
        })
        if len(items) >= max(1, min(int(limit), 50)):
            break
    return {"status": "ok", "scope": "catalog", "count": len(items), "items": items, "truncated": len(items) >= max(1, min(int(limit), 50))}


def note_sources(*, pdf: Path, inbox_root: Path) -> list[dict[str, Any]]:
    root = Path(inbox_root).resolve(strict=False)
    result = []
    for path in _notes_for_pdf(pdf.resolve(strict=False), root):
        stat = path.stat()
        result.append({"relative_path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "suffix": path.suffix.lower()})
    return result
