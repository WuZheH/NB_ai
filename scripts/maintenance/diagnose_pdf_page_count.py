from __future__ import annotations

import argparse
import importlib.util
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / ".codex_tmp" / "pml_page_count_diagnosis"
IMPORT_JOBS_DIR = PROJECT_ROOT / ".codex_tmp" / "import_jobs"


def _optional_page_counts(pdf_path: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if importlib.util.find_spec("pypdf") is not None:
        from pypdf import PdfReader

        results.append(
            {
                "tool": "pypdf",
                "reported_page_count": len(PdfReader(str(pdf_path)).pages),
                "page_system": "physical_pdf_pages",
                "available": True,
            }
        )
    else:
        results.append({"tool": "pypdf", "reported_page_count": None, "available": False})

    if importlib.util.find_spec("pypdfium2") is not None:
        import pypdfium2

        document = pypdfium2.PdfDocument(str(pdf_path))
        try:
            page_count = len(document)
        finally:
            document.close()
        results.append(
            {
                "tool": "pypdfium2",
                "reported_page_count": page_count,
                "page_system": "physical_pdf_pages",
                "available": True,
            }
        )
    else:
        results.append({"tool": "pypdfium2", "reported_page_count": None, "available": False})
    return results


def _xref_reference(value: tuple[str, str]) -> int | None:
    if value[0] != "xref":
        return None
    match = re.match(r"(\d+)\s+0\s+R", value[1])
    return int(match.group(1)) if match else None


def _chapter_one_range(toc: list[list[Any]], page_count: int) -> dict[str, Any] | None:
    chapter_index = next(
        (
            index
            for index, entry in enumerate(toc)
            if re.search(r"\bchapter\s+1\b|^\s*1(?:\.|\s)", str(entry[1]), re.IGNORECASE)
        ),
        None,
    )
    if chapter_index is None:
        return None
    level, title, start = toc[chapter_index][:3]
    end = page_count
    for entry in toc[chapter_index + 1 :]:
        if int(entry[0]) <= int(level):
            end = int(entry[2]) - 1
            break
    return {"title": title, "physical_page_start": int(start), "physical_page_end": end}


def _matching_import_jobs(pdf_path: Path) -> list[dict[str, Any]]:
    if not IMPORT_JOBS_DIR.is_dir():
        return []
    normalized_target = str(pdf_path.resolve()).casefold()
    results: list[dict[str, Any]] = []
    for payload_path in IMPORT_JOBS_DIR.glob("*/payload.json"):
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        candidate = str(Path(str(payload.get("pdf_path") or "")).resolve()).casefold()
        if candidate != normalized_target:
            continue
        status_path = payload_path.parent / "status.json"
        try:
            status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            status = {}
        results.append(
            {
                "job_id": status.get("job_id") or payload_path.parent.name,
                "status": status.get("status"),
                "payload_path": str(payload_path),
                "status_path": str(status_path) if status_path.is_file() else None,
                "confirm_page_count": payload.get("confirm_page_count"),
                "import_granularity": payload.get("import_granularity"),
                "total_units": status.get("total_units"),
                "parser_backend": status.get("parser_backend") or payload.get("backend"),
            }
        )
    return sorted(results, key=lambda item: str(item["job_id"]))


def diagnose_pdf(pdf_path: Path) -> dict[str, Any]:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    with fitz.open(pdf_path) as document:
        page_count = document.page_count
        labels = [document[index].get_label() for index in range(page_count)]
        toc = document.get_toc(simple=True)
        catalog_xref = document.pdf_catalog()
        pages_key = document.xref_get_key(catalog_xref, "Pages")
        pages_xref = _xref_reference(pages_key)
        page_tree_count = document.xref_get_key(pages_xref, "Count")[1] if pages_xref else None
        page_labels_key = document.xref_get_key(catalog_xref, "PageLabels")
        oc_properties_key = document.xref_get_key(catalog_xref, "OCProperties")
        optional_content_groups = document.get_ocgs()
        page_xrefs = [document.page_xref(index) for index in range(page_count)]
        label_counts = Counter(labels)
        label_678_indices = [index + 1 for index, value in enumerate(labels) if value == "678"]

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pdf_path": str(pdf_path),
        "tools": [
            {
                "tool": "PyMuPDF",
                "reported_page_count": page_count,
                "page_system": "physical_pdf_pages",
                "code_path": "fitz.open(pdf_path).page_count",
                "available": True,
            },
            *_optional_page_counts(pdf_path),
        ],
        "pdf_structure": {
            "physical_page_count": page_count,
            "catalog_xref": catalog_xref,
            "pages_tree_reference": pages_key[1],
            "pages_tree_count": int(page_tree_count) if str(page_tree_count).isdigit() else page_tree_count,
            "page_labels_dictionary": {"type": page_labels_key[0], "value": page_labels_key[1]},
            "has_page_labels": page_labels_key[0] not in {"null", "none"},
            "first_20_page_labels": [
                {"physical_page": index + 1, "label": label} for index, label in enumerate(labels[:20])
            ],
            "last_page_label": labels[-1] if labels else None,
            "unique_page_label_count": len(label_counts),
            "duplicate_page_label_count": sum(count - 1 for count in label_counts.values() if count > 1),
            "physical_pages_labeled_678": label_678_indices,
            "outline_entry_count": len(toc),
            "outline_first_10": [
                {"level": int(level), "title": str(title), "physical_page": int(page)}
                for level, title, page in toc[:10]
            ],
            "chapter_1_physical_page_range": _chapter_one_range(toc, page_count),
            "unique_page_object_reference_count": len(set(page_xrefs)),
            "repeated_page_object_reference_count": page_count - len(set(page_xrefs)),
            "optional_content_properties": {"type": oc_properties_key[0], "value": oc_properties_key[1]},
            "optional_content_group_count": len(optional_content_groups),
        },
        "interpretation": {
            "physical_pages_are_page_tree_leaves": True,
            "page_labels_are_display_labels_not_an_alternative_page_tree": True,
            "page_678_possible_label_positions": label_678_indices,
        },
        "matching_import_job_artifacts": _matching_import_jobs(pdf_path),
        "safety": {
            "sqlite_written": False,
            "vector_store_written": False,
            "ocr_executed": False,
            "marker_executed": False,
            "llm_called": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only physical page-count and PDF structure diagnosis.")
    parser.add_argument("--pdf-path", required=True, type=Path)
    parser.add_argument("--json", action="store_true", dest="emit_json")
    args = parser.parse_args()

    report = diagnose_pdf(args.pdf_path)
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DEFAULT_OUTPUT_DIR / "pdf_page_count_diagnosis.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["output_path"] = str(output_path)

    if args.emit_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"physical_page_count={report['pdf_structure']['physical_page_count']}")
        print(f"report_path={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
