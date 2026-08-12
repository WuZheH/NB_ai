from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from app.core.paths import CONVERTED_MD_DIR, DATA_PROJECT_ROOT, OUTPUTS_DIR, PROJECT_ROOT


PYMUPDF_BACKEND_NAME = "pymupdf"
PDF_BACKEND_UNAVAILABLE_ERROR = "pdf_backend_unavailable"
PDF_BACKEND_UNAVAILABLE_MESSAGE = (
    "PyMuPDF backend is unavailable. Lightweight PDF preview/classification is disabled, "
    "but converted-md import may still be available."
)
FALLBACK_ROUTES = ["converted_md", "marker_output", "manual_md_import"]


class PdfBackendUnavailableError(RuntimeError):
    error = PDF_BACKEND_UNAVAILABLE_ERROR
    backend = PYMUPDF_BACKEND_NAME
    message = PDF_BACKEND_UNAVAILABLE_MESSAGE
    db_write_performed = False
    llm_called = False
    mechanism_generated = False
    vector_store_write_performed = False

    def __init__(self, original_error: BaseException | str):
        self.original_error = str(original_error)
        super().__init__(self.message)

    def to_response(self, *, source_path: str | Path | None = None) -> dict[str, Any]:
        fallback = converted_md_fallback_status(source_path)
        return {
            "status": "BLOCKED",
            "error": self.error,
            "backend": self.backend,
            "original_error": self.original_error,
            "message": self.message,
            "fallback_available": True,
            "fallback_routes": list(FALLBACK_ROUTES),
            **fallback,
            "db_write_performed": False,
            "core_db_write_performed": False,
            "llm_called": False,
            "external_llm_called": False,
            "mechanism_generated": False,
            "final_hypothesis_created": False,
            "vector_store_write_performed": False,
            "marker_executed": False,
            "ocr_executed": False,
        }


def load_fitz_backend() -> Any:
    try:
        return importlib.import_module("fitz")
    except Exception as exc:
        raise PdfBackendUnavailableError(exc) from exc


def converted_md_fallback_status(source_path: str | Path | None = None) -> dict[str, Any]:
    candidates = _converted_md_candidates(source_path)
    converted = next((path for path in candidates if _is_project_file(path)), None)
    staging = _staging_markdown_for_source(source_path)
    available_path = converted or staging
    available = available_path is not None
    return {
        "converted_md_available": available,
        "converted_md_exists": available,
        "converted_md_path": _relative_or_none(available_path),
        "staging_markdown_available": staging is not None,
        "staging_markdown_path": _relative_or_none(staging),
        "marker_output_available": converted is not None,
        "manual_md_import_available": True,
        "next_action": "use_converted_md_import" if available else "run_pdf_to_md_conversion",
        "next_action_label": (
            "Use existing converted Markdown import"
            if available
            else "Generate Markdown before importing"
        ),
    }


def _converted_md_candidates(source_path: str | Path | None) -> list[Path]:
    if source_path is None:
        return []
    source = Path(source_path)
    stem = source.stem
    candidates: list[Path] = []
    if source.suffix.lower() == ".md":
        candidates.append(source.resolve(strict=False))
    if source.parent:
        candidates.extend(
            [
                source.with_suffix(".md").resolve(strict=False),
                source.with_name(f"{stem}.auto.md").resolve(strict=False),
                source.with_name(f"{stem}_converted.md").resolve(strict=False),
                source.with_name(f"{stem}_cleaned.md").resolve(strict=False),
            ]
        )
    converted_root = CONVERTED_MD_DIR
    if converted_root.is_dir() and stem:
        for pattern in (f"{stem}.md", f"{stem}*.md", f"*{stem}*.md"):
            candidates.extend(path.resolve(strict=False) for path in converted_root.rglob(pattern))
    return _dedupe_paths([path for path in candidates if path.is_file()])


def _staging_markdown_for_source(source_path: str | Path | None) -> Path | None:
    staging_root = OUTPUTS_DIR / "import_staging"
    if not staging_root.is_dir():
        return None
    source_stem = Path(source_path).stem if source_path else ""
    matches: list[Path] = []
    for paper in staging_root.glob("*/paper.md"):
        if not paper.is_file():
            continue
        if not source_stem:
            matches.append(paper)
            continue
        try:
            text = paper.read_text(encoding="utf-8", errors="ignore")[:3000]
        except OSError:
            continue
        if source_stem in text or source_stem in paper.parent.name:
            matches.append(paper)
    if not matches:
        return None
    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0].resolve(strict=False)


def _is_project_file(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() != ".md":
        return False
    try:
        resolved = path.resolve(strict=False)
        return any(
            resolved.is_relative_to(root.resolve(strict=False))
            for root in (PROJECT_ROOT, DATA_PROJECT_ROOT)
        )
    except ValueError:
        return False


def _relative_or_none(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve(strict=False)
    try:
        return str(resolved.relative_to(DATA_PROJECT_ROOT.resolve(strict=False))).replace("\\", "/")
    except ValueError:
        try:
            return str(resolved.relative_to(PROJECT_ROOT.resolve(strict=False))).replace("\\", "/")
        except ValueError:
            return str(resolved)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    output: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve(strict=False)).casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(path)
    return output


__all__ = [
    "FALLBACK_ROUTES",
    "PDF_BACKEND_UNAVAILABLE_ERROR",
    "PDF_BACKEND_UNAVAILABLE_MESSAGE",
    "PYMUPDF_BACKEND_NAME",
    "PdfBackendUnavailableError",
    "converted_md_fallback_status",
    "load_fitz_backend",
]
