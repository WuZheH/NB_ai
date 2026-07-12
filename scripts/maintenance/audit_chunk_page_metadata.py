from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.paths import DEFAULT_DB_PATH, PROJECT_ROOT
from app.services.library_service import is_metadata_chunk_text
from app.services.pdf_chunk_locator_service import _normalize_chunk_tokens


MIN_AUDIT_TOKENS = 20
MISMATCH_MIN_SCORE = 0.55
MISMATCH_MIN_DELTA = 0.25


@dataclass(frozen=True)
class ChunkAuditItem:
    chunk_id: int
    document_id: int
    title: str
    heading_path: str
    chunk_text: str
    pdf_page_start: int | None
    pdf_page_end: int | None
    pdf_path: str | None


@dataclass(frozen=True)
class ChunkAuditResult:
    category: str
    chunk_id: int | None = None
    document_id: int | None = None
    title: str = ""
    heading_path: str = ""
    db_pdf_page: int | None = None
    best_pdf_page: int | None = None
    db_score: float = 0.0
    best_score: float = 0.0
    delta: int | None = None
    reason: str | None = None
    chunk_text_preview: str = ""


def resolve_pdf_path(pdf_path: str | None) -> Path | None:
    if not pdf_path:
        return None
    candidate = Path(pdf_path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def page_text_score(chunk_text: str, page_text: str) -> float:
    chunk_tokens = _normalize_chunk_tokens(chunk_text)
    page_tokens = _normalize_chunk_tokens(_normalize_pdf_text(page_text))
    if len(chunk_tokens) < MIN_AUDIT_TOKENS or not page_tokens:
        return 0.0
    page_token_set = set(page_tokens)
    best = 0.0
    for target in _audit_targets(chunk_tokens):
        if not target:
            continue
        target_set = set(target)
        coverage = len(target_set & page_token_set) / max(len(target_set), 1)
        sequence = _longest_contiguous_match_ratio(target, page_tokens)
        best = max(best, coverage * 0.85, sequence)
    return round(best, 3)


def classify_page_scores(db_page: int, scores: dict[int, float]) -> ChunkAuditResult:
    db_score = scores.get(db_page, 0.0)
    best_page, best_score = max(scores.items(), key=lambda item: item[1])
    delta = best_page - db_page
    if best_page == db_page:
        return ChunkAuditResult(category="ok_db_page_best", db_pdf_page=db_page, best_pdf_page=best_page, db_score=db_score, best_score=best_score, delta=0)
    if best_score >= MISMATCH_MIN_SCORE and best_score - db_score >= MISMATCH_MIN_DELTA:
        category = "likely_mismatch_plus_1" if delta == 1 else "likely_mismatch_minus_1" if delta == -1 else "likely_mismatch_other"
        return ChunkAuditResult(category=category, db_pdf_page=db_page, best_pdf_page=best_page, db_score=db_score, best_score=best_score, delta=delta)
    return ChunkAuditResult(category="uncertain", db_pdf_page=db_page, best_pdf_page=best_page, db_score=db_score, best_score=best_score, delta=delta)


def should_skip_item(item: ChunkAuditItem) -> ChunkAuditResult | None:
    text = " ".join((item.chunk_text or "").split())
    if not item.pdf_page_start:
        return _skip_result(item, "skipped_no_pdf_page", "pdf_page_start_empty")
    if is_metadata_chunk_text(text) or _looks_like_metadata_or_reference(text, item.heading_path):
        return _skip_result(item, "skipped_short_or_metadata", "metadata_or_reference_like")
    if len(_normalize_chunk_tokens(text)) < MIN_AUDIT_TOKENS:
        return _skip_result(item, "skipped_short_or_metadata", "too_few_tokens")
    resolved_pdf = resolve_pdf_path(item.pdf_path)
    if resolved_pdf is None or not resolved_pdf.exists() or resolved_pdf.suffix.lower() != ".pdf":
        return _skip_result(item, "skipped_pdf_missing", "pdf_missing")
    return None


def audit_item(item: ChunkAuditItem, pdf_doc: Any, adjacent_window: int = 1) -> ChunkAuditResult:
    skip = should_skip_item(item)
    if skip is not None:
        return skip
    assert item.pdf_page_start is not None
    scores: dict[int, float] = {}
    for page_number in range(item.pdf_page_start - adjacent_window, item.pdf_page_start + adjacent_window + 1):
        page_index = page_number - 1
        if page_number < 1 or page_index >= pdf_doc.page_count:
            continue
        try:
            page_text = pdf_doc.load_page(page_index).get_text("text")
        except Exception as exc:  # pragma: no cover - depends on external PDFs
            return _skip_result(item, "error", f"pdf_page_read_failed:{exc}")
        scores[page_number] = page_text_score(item.chunk_text, page_text)
    if not scores:
        return _skip_result(item, "error", "no_pages_scored")
    classified = classify_page_scores(item.pdf_page_start, scores)
    return ChunkAuditResult(
        **{
            **asdict(classified),
            "chunk_id": item.chunk_id,
            "document_id": item.document_id,
            "title": item.title,
            "heading_path": item.heading_path,
            "chunk_text_preview": _preview(item.chunk_text),
        }
    )


def audit_item_page_texts(item: ChunkAuditItem, page_texts: dict[int, str]) -> ChunkAuditResult:
    if item.pdf_page_start is None:
        return _skip_result(item, "skipped_no_pdf_page", "pdf_page_start_empty")
    scores = {page_number: page_text_score(item.chunk_text, text) for page_number, text in page_texts.items()}
    if not scores:
        return _skip_result(item, "error", "no_pages_scored")
    classified = classify_page_scores(item.pdf_page_start, scores)
    return ChunkAuditResult(
        **{
            **asdict(classified),
            "chunk_id": item.chunk_id,
            "document_id": item.document_id,
            "title": item.title,
            "heading_path": item.heading_path,
            "chunk_text_preview": _preview(item.chunk_text),
        }
    )


def audit_chunks(
    *,
    document_id: int | None = None,
    limit: int | None = None,
    adjacent_window: int = 1,
) -> list[ChunkAuditResult]:
    try:
        import fitz  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("PyMuPDF/fitz is required for PDF read-only audit.") from exc

    items = load_chunk_items(document_id=document_id, limit=limit)
    results: list[ChunkAuditResult] = []
    open_pdfs: dict[Path, Any] = {}
    page_text_cache: dict[tuple[Path, int], str] = {}
    try:
        for item in items:
            skip = should_skip_item(item)
            if skip is not None:
                results.append(skip)
                continue
            pdf_path = resolve_pdf_path(item.pdf_path)
            assert pdf_path is not None
            try:
                pdf_doc = open_pdfs.get(pdf_path)
                if pdf_doc is None:
                    pdf_doc = fitz.open(str(pdf_path))
                    open_pdfs[pdf_path] = pdf_doc
                assert item.pdf_page_start is not None
                page_texts: dict[int, str] = {}
                for page_number in range(item.pdf_page_start - adjacent_window, item.pdf_page_start + adjacent_window + 1):
                    page_index = page_number - 1
                    if page_number < 1 or page_index >= pdf_doc.page_count:
                        continue
                    cache_key = (pdf_path, page_number)
                    if cache_key not in page_text_cache:
                        page_text_cache[cache_key] = pdf_doc.load_page(page_index).get_text("text")
                    page_texts[page_number] = page_text_cache[cache_key]
                results.append(audit_item_page_texts(item, page_texts))
            except Exception as exc:  # pragma: no cover - depends on external PDFs
                results.append(_skip_result(item, "error", f"pdf_open_failed:{exc}"))
    finally:
        for pdf_doc in open_pdfs.values():
            try:
                pdf_doc.close()
            except Exception:
                pass
    return results


def load_chunk_items(*, document_id: int | None = None, limit: int | None = None) -> list[ChunkAuditItem]:
    db_path = DEFAULT_DB_PATH.resolve()
    uri = f"file:{db_path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if document_id is not None:
            clauses.append("kc.document_id = ?")
            params.append(document_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_clause = "LIMIT ?" if limit is not None else ""
        if limit is not None:
            params.append(limit)
        rows = con.execute(
            f"""
            SELECT
                kc.id AS chunk_id,
                kc.document_id AS document_id,
                d.title AS title,
                kc.heading_path AS heading_path,
                kc.chunk_text AS chunk_text,
                kc.pdf_page_start AS pdf_page_start,
                kc.pdf_page_end AS pdf_page_end,
                COALESCE(kc.pdf_path, d.pdf_path) AS pdf_path
            FROM knowledge_chunks kc
            JOIN documents d ON d.id = kc.document_id
            {where}
            ORDER BY kc.document_id, kc.id
            {limit_clause}
            """,
            params,
        ).fetchall()
        return [
            ChunkAuditItem(
                chunk_id=int(row["chunk_id"]),
                document_id=int(row["document_id"]),
                title=str(row["title"] or ""),
                heading_path=str(row["heading_path"] or ""),
                chunk_text=str(row["chunk_text"] or ""),
                pdf_page_start=row["pdf_page_start"],
                pdf_page_end=row["pdf_page_end"],
                pdf_path=row["pdf_path"],
            )
            for row in rows
        ]
    finally:
        con.close()


def summarize_results(results: list[ChunkAuditResult], sample_mismatches: int = 20) -> dict[str, Any]:
    counts = Counter(result.category for result in results)
    likely = [r for r in results if r.category.startswith("likely_mismatch")]
    auditable_categories = {"ok_db_page_best", "likely_mismatch_plus_1", "likely_mismatch_minus_1", "likely_mismatch_other", "uncertain"}
    by_doc: dict[int, dict[str, Any]] = defaultdict(lambda: {"title": "", "auditable": 0, "mismatch_count": 0})
    for result in results:
        if result.document_id is None:
            continue
        doc = by_doc[result.document_id]
        doc["title"] = result.title
        if result.category in auditable_categories:
            doc["auditable"] += 1
        if result.category.startswith("likely_mismatch"):
            doc["mismatch_count"] += 1
    mismatch_by_document = []
    for doc_id, info in sorted(by_doc.items()):
        auditable = int(info["auditable"])
        mismatch_count = int(info["mismatch_count"])
        mismatch_by_document.append(
            {
                "document_id": doc_id,
                "title": info["title"],
                "auditable": auditable,
                "mismatch_count": mismatch_count,
                "mismatch_rate": round(mismatch_count / auditable, 3) if auditable else 0.0,
            }
        )
    mismatch_by_document.sort(key=lambda item: item["mismatch_count"], reverse=True)
    return {
        "total_chunks": len(results),
        "auditable_chunks": sum(counts.get(category, 0) for category in auditable_categories),
        "skipped_chunks": sum(count for category, count in counts.items() if category.startswith("skipped")),
        "ok_count": counts.get("ok_db_page_best", 0),
        "likely_mismatch_count": len(likely),
        "uncertain_count": counts.get("uncertain", 0),
        "error_count": counts.get("error", 0),
        "mismatch_by_delta": dict(Counter(str(result.delta) for result in likely)),
        "mismatch_by_document": mismatch_by_document,
        "mismatch_samples": [asdict(result) for result in likely[:sample_mismatches]],
        "category_counts": dict(counts),
    }


def print_summary(summary: dict[str, Any], include_uncertain: bool, results: list[ChunkAuditResult]) -> None:
    for key in (
        "total_chunks",
        "auditable_chunks",
        "skipped_chunks",
        "ok_count",
        "likely_mismatch_count",
        "uncertain_count",
        "error_count",
    ):
        print(f"{key}: {summary[key]}")
    print("mismatch_by_delta:")
    for delta, count in sorted(summary["mismatch_by_delta"].items()):
        print(f"  {delta}: {count}")
    print("mismatch_by_document:")
    for item in summary["mismatch_by_document"]:
        if item["mismatch_count"] or item["auditable"]:
            print(
                f"  doc {item['document_id']}: mismatch={item['mismatch_count']}/"
                f"{item['auditable']} rate={item['mismatch_rate']} title={item['title']}"
            )
    print("high_confidence_mismatch_samples:")
    for sample in summary["mismatch_samples"]:
        print(
            f"  doc={sample['document_id']} chunk={sample['chunk_id']} db_page={sample['db_pdf_page']} "
            f"best_page={sample['best_pdf_page']} db_score={sample['db_score']} best_score={sample['best_score']} "
            f"delta={sample['delta']} heading={sample['heading_path']} text={sample['chunk_text_preview']}"
        )
    if include_uncertain:
        print("uncertain_samples:")
        for result in [r for r in results if r.category == "uncertain"][:20]:
            print(
                f"  doc={result.document_id} chunk={result.chunk_id} db_page={result.db_pdf_page} "
                f"best_page={result.best_pdf_page} db_score={result.db_score} best_score={result.best_score} "
                f"delta={result.delta} text={result.chunk_text_preview}"
            )


def _skip_result(item: ChunkAuditItem, category: str, reason: str) -> ChunkAuditResult:
    return ChunkAuditResult(
        category=category,
        chunk_id=item.chunk_id,
        document_id=item.document_id,
        title=item.title,
        heading_path=item.heading_path,
        db_pdf_page=item.pdf_page_start,
        reason=reason,
        chunk_text_preview=_preview(item.chunk_text),
    )


def _normalize_pdf_text(text: str) -> str:
    return (
        str(text or "")
        .replace("\ufb01", "fi")
        .replace("\ufb02", "fl")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )


def _audit_targets(chunk_tokens: list[str]) -> list[list[str]]:
    targets: list[list[str]] = []
    for start in (0, min(8, max(len(chunk_tokens) - 20, 0)), len(chunk_tokens) // 4, len(chunk_tokens) // 2):
        for length in (96, 72, 48, 32, 20):
            end = min(len(chunk_tokens), start + length)
            if end - start >= MIN_AUDIT_TOKENS:
                target = chunk_tokens[start:end]
                if target not in targets:
                    targets.append(target)
    if len(chunk_tokens) >= MIN_AUDIT_TOKENS:
        targets.append(chunk_tokens[: min(len(chunk_tokens), 96)])
    return targets


def _longest_contiguous_match_ratio(target: list[str], page_tokens: list[str]) -> float:
    target_positions: dict[str, list[int]] = defaultdict(list)
    for index, token in enumerate(target):
        target_positions[token].append(index)
    best = 0
    active: dict[int, int] = {}
    for token in page_tokens:
        next_active: dict[int, int] = {}
        for target_index in target_positions.get(token, []):
            length = active.get(target_index - 1, 0) + 1
            next_active[target_index] = max(next_active.get(target_index, 0), length)
            best = max(best, length)
        active = next_active
    return best / max(len(target), 1)


def _looks_like_metadata_or_reference(text: str, heading_path: str = "") -> bool:
    normalized = " ".join(str(text or "").split()).lower()
    heading = str(heading_path or "").lower()
    if not normalized:
        return True
    if "references" in heading or normalized.startswith(("references", "reference", "acknowledgements", "acknowledgments")):
        return True
    alpha_count = sum(1 for char in normalized if char.isalpha())
    return alpha_count < max(20, len(normalized) * 0.25)


def _preview(text: str, limit: int = 180) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only audit for existing knowledge_chunks PDF page metadata.")
    parser.add_argument("--document-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sample-mismatches", type=int, default=20)
    parser.add_argument("--adjacent-window", type=int, default=1)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--include-uncertain", action="store_true")
    args = parser.parse_args()

    results = audit_chunks(document_id=args.document_id, limit=args.limit, adjacent_window=args.adjacent_window)
    summary = summarize_results(results, sample_mismatches=args.sample_mismatches)
    if args.json_output:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print_summary(summary, include_uncertain=args.include_uncertain, results=results)


if __name__ == "__main__":
    main()
