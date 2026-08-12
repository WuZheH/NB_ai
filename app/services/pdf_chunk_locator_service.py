from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
import re
import sqlite3
from typing import Any

from sqlalchemy import select

from app.core.paths import DEFAULT_DB_PATH
from app.db.session import SessionLocal
from app.models import Document, KnowledgeChunk
from app.services.pdf_layout_service import load_layout_location_for_chunk
from app.services.library_service import (
    evidence_locator_contract,
    is_metadata_chunk_text,
    resolve_safe_pdf_path,
)


SNIPPET_LIMIT = 220


@dataclass(frozen=True)
class PdfChunkLocatorResult:
    status: str
    locator_status: str
    locator_reason: str
    is_metadata_chunk: bool
    is_locatable: bool
    document_id: int | None
    chunk_id: int
    pdf_page: int | None
    page_index: int | None
    match_method: str
    confidence: str
    rects: list[dict[str, float]]
    page_width: float | None
    page_height: float | None
    snippet_used: str
    warnings: list[str]
    highlight_count: int
    matched_term: str | None = None
    matched_lines: list[str] = field(default_factory=list)
    original_pdf_page: int | None = None
    corrected_pdf_page: int | None = None
    page_metadata_mismatch: bool = False
    visual_mode: str = "page_only"
    is_exact_text_highlight: bool = False
    is_layout_text_highlight: bool = False
    approximate_region: dict[str, float] | None = None
    page_text_length: int | None = None


def normalize_locator_text(text: str) -> str:
    return " ".join(str(text or "").split())


def _normalize_chunk_tokens(text: str) -> list[str]:
    """Normalize chunk text into a token sequence for alignment.

    - lowercase
    - collapse whitespace
    - remove citation markers like [45], [1,2,3]
    - remove very short tokens (< 2 chars)
    - preserve word boundaries
    """
    raw = normalize_locator_text(text)
    # Remove citation markers: [45], [1,2,3], [12, 34]
    cleaned = re.sub(r"\[[\d,\s]+\]", " ", raw)
    # Remove standalone reference numbers
    cleaned = re.sub(r"\s+\d{1,3}\s+", " ", f" {cleaned} ")
    # Collapse whitespace again
    cleaned = " ".join(cleaned.split())
    tokens = re.findall(r"[a-z0-9]+", cleaned.lower())
    # Keep only tokens with meaningful content
    return [t for t in tokens if len(t) >= 2]


def _normalize_pdf_word_token(word: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", str(word or "").lower())
    return "".join(tokens)


def _extract_page_token_spans(page: Any) -> list[dict[str, Any]]:
    """Extract word-level tokens with their bounding boxes from a PDF page.

    Uses get_text("words") which returns (x0, y0, x1, y1, word, ...) per word.
    Returns list of dicts with 'token', 'x0', 'y0', 'x1', 'y1'.
    """
    spans: list[dict[str, Any]] = []
    try:
        words = page.get_text("words")
    except Exception:
        return spans
    index = 0
    while index < len(words):
        word_info = words[index]
        if len(word_info) < 5:
            index += 1
            continue
        x0, y0, x1, y1, word = word_info[0], word_info[1], word_info[2], word_info[3], str(word_info[4])
        token = _normalize_pdf_word_token(word)
        raw_word = word.strip()
        if raw_word.endswith("-") and index + 1 < len(words) and len(words[index + 1]) >= 5:
            next_info = words[index + 1]
            next_word = str(next_info[4])
            joined = _normalize_pdf_word_token(raw_word[:-1] + next_word)
            if joined:
                token = joined
                x0 = min(float(x0), float(next_info[0]))
                y0 = min(float(y0), float(next_info[1]))
                x1 = max(float(x1), float(next_info[2]))
                y1 = max(float(y1), float(next_info[3]))
                index += 1
        if not token or len(token) < 2:
            index += 1
            continue
        spans.append({"token": token, "x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1)})
        index += 1
    return spans


def _align_chunk_to_page_tokens(
    chunk_tokens: list[str],
    page_spans: list[dict[str, Any]],
    *,
    min_match_ratio: float = 0.42,
    min_anchor_tokens: int = 4,
    skip_prefix_max: int = 8,
    skip_suffix_max: int = 4,
) -> dict[str, Any]:
    """Find the best contiguous window of page tokens matching chunk tokens.

    Strategy:
    1. Try to find a matching window using the full chunk token sequence.
    2. If that fails, try skipping a prefix of chunk tokens (up to skip_prefix_max)
       to handle partial/fragmented chunk starts.
    3. Similarly, try skipping a suffix for fragmented chunk ends.
    4. Score each window by token match count and return the best.

    Returns dict with 'rects', 'match_count', 'total_chunk_tokens', 'match_ratio',
    'snippet_used'.
    """
    if not chunk_tokens or not page_spans:
        return {"rects": [], "match_count": 0, "total_chunk_tokens": len(chunk_tokens), "match_ratio": 0.0, "snippet_used": ""}

    page_tokens = [s["token"] for s in page_spans]
    chunk_len = len(chunk_tokens)
    page_len = len(page_tokens)

    best = {"start": -1, "end": -1, "matches": 0, "ratio": 0.0, "coverage": 0.0}

    for target in _alignment_targets(chunk_tokens, page_len, skip_prefix_max, skip_suffix_max, min_anchor_tokens):
        target_len = len(target)
        if target_len < min_anchor_tokens or target_len > page_len:
            continue
        for window_start in range(0, page_len - target_len + 1):
            window = page_tokens[window_start : window_start + target_len]
            exact_matches = sum(1 for left, right in zip(target, window) if left == right)
            overlap = len(set(target) & set(window))
            sequence_ratio = SequenceMatcher(None, target, window, autojunk=False).ratio()
            ratio = max(exact_matches / target_len, sequence_ratio, overlap / max(len(set(target)), 1) * 0.85)
            coverage = overlap / max(len(set(target)), 1)
            better = ratio > best["ratio"] or (
                ratio == best["ratio"] and (coverage, exact_matches) > (best["coverage"], best["matches"])
            )
            if better:
                best = {
                    "start": window_start,
                    "end": window_start + target_len,
                    "matches": max(exact_matches, overlap),
                    "ratio": ratio,
                    "coverage": coverage,
                }

    if best["ratio"] >= min_match_ratio and best["coverage"] >= 0.38 and best["matches"] >= min_anchor_tokens:
        matched_spans = page_spans[best["start"] : best["end"]]
        rects = _merge_nearby_rects(matched_spans)
        snippet = " ".join(s["token"] for s in matched_spans)
        return {
            "rects": rects,
            "match_count": best["matches"],
            "total_chunk_tokens": chunk_len,
            "match_ratio": round(best["ratio"], 3),
            "coverage": round(best["coverage"], 3),
            "snippet_used": _cap_snippet(snippet),
        }

    return {"rects": [], "match_count": best["matches"], "total_chunk_tokens": chunk_len, "match_ratio": round(best["ratio"], 3), "coverage": round(best["coverage"], 3), "snippet_used": ""}


def _alignment_targets(
    chunk_tokens: list[str],
    page_len: int,
    skip_prefix_max: int,
    skip_suffix_max: int,
    min_anchor_tokens: int,
) -> list[list[str]]:
    chunk_len = len(chunk_tokens)
    if chunk_len < min_anchor_tokens:
        return []
    target_lengths = [min(chunk_len, page_len, value) for value in (96, 72, 48, 32, 20, 12)]
    target_lengths = sorted({value for value in target_lengths if value >= min_anchor_tokens}, reverse=True)
    starts = {0}
    starts.update(range(1, min(skip_prefix_max, chunk_len - min_anchor_tokens) + 1))
    if chunk_len > 24:
        starts.update({chunk_len // 4, chunk_len // 3, chunk_len // 2})
    targets: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for start in sorted(starts):
        for length in target_lengths:
            end = min(chunk_len, start + length)
            if end - start < min_anchor_tokens:
                continue
            target = chunk_tokens[start:end]
            key = tuple(target)
            if key not in seen:
                seen.add(key)
                targets.append(target)
    for suffix in range(1, min(skip_suffix_max, chunk_len - min_anchor_tokens) + 1):
        target = chunk_tokens[: chunk_len - suffix]
        if len(target) > page_len:
            target = target[:page_len]
        key = tuple(target)
        if len(target) >= min_anchor_tokens and key not in seen:
            seen.add(key)
            targets.append(target)
    return targets


def _merge_nearby_rects(spans: list[dict[str, Any]], gap_threshold: float = 8.0) -> list[dict[str, float]]:
    """Merge adjacent word-level rects into larger highlight rectangles.

    Words on the same line (y0 within threshold) and close horizontally
    are merged into a single rect.
    """
    if not spans:
        return []
    merged: list[dict[str, float]] = []
    current = None
    for span in spans:
        if current is None:
            current = {"x0": span["x0"], "y0": span["y0"], "x1": span["x1"], "y1": span["y1"]}
            continue
        # Same line check
        same_line = abs(current["y0"] - span["y0"]) < gap_threshold
        close_horizontally = span["x0"] - current["x1"] < gap_threshold * 6
        if same_line and close_horizontally:
            current["x1"] = span["x1"]
            current["y1"] = max(current["y1"], span["y1"])
        else:
            merged.append(current)
            current = {"x0": span["x0"], "y0": span["y0"], "x1": span["x1"], "y1": span["y1"]}
    if current is not None:
        merged.append(current)
    return merged


def locate_chunk_in_pdf_page(
    document_id: int,
    chunk_id: int,
    fallback_terms: list[str] | None = None,
) -> PdfChunkLocatorResult:
    try:
        import fitz  # type: ignore
    except ImportError:
        return _result(
            status="dependency_unavailable",
            document_id=document_id,
            chunk_id=chunk_id,
            warnings=["pymupdf_unavailable"],
        )

    chunk, document = _load_chunk_and_document(document_id, chunk_id)
    pdf_page = chunk.pdf_page_start
    pdf_path_value = chunk.pdf_path or document.pdf_path
    contract = evidence_locator_contract(
        chunk_text=chunk.chunk_text,
        pdf_page_start=pdf_page,
        pdf_path=pdf_path_value,
        is_metadata=is_metadata_chunk_text(chunk.chunk_text),
    )
    if contract["locator_status"] in {"metadata_non_locatable", "no_page", "no_text", "pdf_missing"}:
        return _result(
            status=str(contract["locator_status"]),
            locator_status=str(contract["locator_status"]),
            locator_reason=str(contract["locator_reason"]),
            is_metadata_chunk=bool(contract["is_metadata_chunk"]),
            is_locatable=False,
            document_id=document.id,
            chunk_id=chunk.id,
            pdf_page=pdf_page,
            page_index=pdf_page - 1 if pdf_page else None,
            snippet_used=chunk.chunk_text,
            warnings=[str(contract["locator_status"])],
        )
    if not pdf_page or pdf_page < 1:
        return _result(
            status="no_page",
            locator_status="no_page",
            locator_reason="该片段缺少 PDF 页码，只能打开文档。",
            is_locatable=False,
            document_id=document.id,
            chunk_id=chunk.id,
            pdf_page=pdf_page,
            warnings=["pdf_page_unavailable"],
        )

    pdf_path = resolve_safe_pdf_path(pdf_path_value)
    if pdf_path is None or not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        return _result(
            status="pdf_missing",
            locator_status="pdf_missing",
            locator_reason="该片段缺少可预览 PDF，只能查看证据文本。",
            is_locatable=False,
            document_id=document.id,
            chunk_id=chunk.id,
            pdf_page=pdf_page,
            page_index=pdf_page - 1,
            warnings=["pdf_unavailable"],
        )

    snippet = _cap_snippet(chunk.chunk_text)
    layout_location = _load_layout_location(document.id, chunk.id)
    if layout_location:
        return _layout_result(
            layout_location,
            document_id=document.id,
            chunk_id=chunk.id,
            snippet=snippet,
        )
    page_index = pdf_page - 1
    try:
        with fitz.open(str(pdf_path)) as pdf:
            if page_index < 0 or page_index >= pdf.page_count:
                return _result(
                    status="page_unavailable",
                    document_id=document.id,
                    chunk_id=chunk.id,
                    pdf_page=pdf_page,
                    page_index=page_index,
                    snippet_used=snippet,
                    warnings=["pdf_page_out_of_range"],
                )
            page = pdf.load_page(page_index)
            page_width = float(page.rect.width)
            page_height = float(page.rect.height)
            page_text_length = len(page.get_text("text") or "")
            located = _locate_text_on_page(page, chunk.chunk_text, fallback_terms=fallback_terms)
            if located["rects"]:
                return _located_result(
                    located,
                    document_id=document.id,
                    chunk_id=chunk.id,
                    pdf_page=pdf_page,
                    page_index=page_index,
                    page_width=page_width,
                    page_height=page_height,
                    page_text_length=page_text_length,
                )
            fallback = _locate_adjacent_page(
                pdf=pdf,
                original_page=pdf_page,
                original_located=located,
                chunk_text=chunk.chunk_text,
                fallback_terms=fallback_terms,
            )
            if fallback:
                return _located_result(
                    fallback["located"],
                    document_id=document.id,
                    chunk_id=chunk.id,
                    pdf_page=fallback["corrected_pdf_page"],
                    page_index=fallback["page_index"],
                    page_width=fallback["page_width"],
                    page_height=fallback["page_height"],
                    page_text_length=fallback.get("page_text_length"),
                    warnings=["page_metadata_mismatch", "adjacent_page_fallback"],
                    original_pdf_page=pdf_page,
                    corrected_pdf_page=fallback["corrected_pdf_page"],
                    page_metadata_mismatch=True,
                )
            return _result(
                status="page_level_only",
                locator_status="page_level_only",
                locator_reason="页码定位成功，精确文本坐标未找到；已显示对应页面。",
                document_id=document.id,
                chunk_id=chunk.id,
                pdf_page=pdf_page,
                page_index=page_index,
                match_method=located["match_method"],
                confidence="none",
                page_width=page_width,
                page_height=page_height,
                page_text_length=page_text_length,
                snippet_used=located["snippet_used"],
                warnings=["chunk_text_not_found_on_page"],
            )
    except Exception:
        return _result(
            status="pdf_missing",
            locator_status="pdf_missing",
            locator_reason="PDF 渲染或读取失败，无法定位该片段。",
            is_locatable=False,
            document_id=document.id,
            chunk_id=chunk.id,
            pdf_page=pdf_page,
            page_index=page_index,
            snippet_used=snippet,
            warnings=["pdf_open_failed"],
        )


def _load_chunk_and_document(document_id: int, chunk_id: int) -> tuple[KnowledgeChunk, Document]:
    with SessionLocal() as session:
        chunk = session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.id == chunk_id, KnowledgeChunk.document_id == document_id)
        ).first()
        if chunk is None:
            raise ValueError(f"Evidence chunk {chunk_id} not found for document {document_id}.")
        document = session.get(Document, document_id)
        if document is None:
            raise ValueError(f"Document {document_id} not found.")
        return chunk, document


def _locate_text_on_page(page: Any, chunk_text: str, fallback_terms: list[str] | None = None) -> dict[str, Any]:
    # Phase 1: exact text search (existing logic)
    snippets = _candidate_snippets(chunk_text)
    for index, snippet in enumerate(snippets):
        rects = _search_for(page, snippet)
        if rects:
            return {
                "rects": rects,
                "match_method": "exact_search" if index == 0 else "short_snippet_search",
                "confidence": "high" if index == 0 else "medium",
                "snippet_used": _cap_snippet(snippet),
            }

    # Phase 2: chunk-level token alignment with word bboxes
    chunk_tokens = _normalize_chunk_tokens(chunk_text)
    page_spans = _extract_page_token_spans(page)
    if chunk_tokens and page_spans:
        aligned = _align_chunk_to_page_tokens(chunk_tokens, page_spans)
        if aligned["rects"]:
            match_ratio = aligned.get("match_ratio", 0.0)
            is_full = match_ratio >= 0.55
            return {
                "rects": aligned["rects"],
                "match_method": "chunk_alignment",
                "confidence": "medium" if is_full else "low",
                "snippet_used": aligned.get("snippet_used") or _cap_snippet(chunk_text),
                "match_ratio": match_ratio,
                "coverage": aligned.get("coverage", 0.0),
                "alignment_status": "chunk_aligned" if is_full else "partial_chunk_aligned",
            }

    # Phase 3: case-insensitive normalized text match (existing fallback, no bboxes)
    page_text = normalize_locator_text(page.get_text("text"))
    chunk_norm = normalize_locator_text(chunk_text)
    short_norm = normalize_locator_text(snippets[-1] if snippets else chunk_text)
    if short_norm and short_norm.casefold() in page_text.casefold():
        return {
            "rects": [],
            "match_method": "normalized_text_match",
            "confidence": "low",
            "snippet_used": _cap_snippet(short_norm),
        }

    # Phase 4: search important fallback terms from the query/snippet when the
    # full chunk text cannot be mapped to PDF coordinates.
    for term in _fallback_search_terms(chunk_text, fallback_terms=fallback_terms):
        rects, matched_term = _search_for_term_variants(page, term)
        if rects:
            return {
                "rects": rects,
                "match_method": "fallback_term_search",
                "confidence": "low",
                "snippet_used": _cap_snippet(matched_term),
                "alignment_status": "fallback_term_found",
                "matched_term": matched_term,
            }

    if not _page_has_extractable_text(page):
        matched_term = _fallback_term_in_chunk_text(chunk_text, fallback_terms=fallback_terms)
        if matched_term:
            return {
                "rects": _page_level_fallback_rect(page),
                "match_method": "fallback_chunk_text_anchor",
                "confidence": "low",
                "snippet_used": _cap_snippet(matched_term),
                "alignment_status": "fallback_term_found",
                "matched_term": matched_term,
                "visual_mode": "approximate_chunk_region",
                "is_exact_text_highlight": False,
            }

    return {
        "rects": [],
        "match_method": "not_found",
        "confidence": "none",
        "snippet_used": _cap_snippet(snippets[-1] if snippets else chunk_text),
        "match_ratio": 0.0,
        "coverage": 0.0,
    }


def _locate_adjacent_page(
    pdf: Any,
    original_page: int,
    original_located: dict[str, Any],
    chunk_text: str,
    fallback_terms: list[str] | None = None,
) -> dict[str, Any] | None:
    if original_located.get("rects"):
        return None
    original_score = _alignment_score(original_located)
    best: dict[str, Any] | None = None
    for candidate_page in (original_page - 1, original_page + 1):
        page_index = candidate_page - 1
        if page_index < 0 or page_index >= pdf.page_count:
            continue
        page = pdf.load_page(page_index)
        located = _locate_text_on_page(page, chunk_text, fallback_terms=fallback_terms)
        if not located.get("rects"):
            continue
        score = _alignment_score(located)
        clearly_better = score >= 0.55 or score - original_score >= 0.25
        if not clearly_better:
            continue
        candidate = {
            "located": located,
            "corrected_pdf_page": candidate_page,
            "page_index": page_index,
            "page_width": _page_width(page),
            "page_height": _page_height(page),
            "page_text_length": len(page.get_text("text") or ""),
            "score": score,
        }
        if best is None or score > best["score"]:
            best = candidate
    return best


def _alignment_score(located: dict[str, Any]) -> float:
    if located.get("rects") and located.get("match_method") in {"exact_search", "short_snippet_search"}:
        return 1.0
    ratio = float(located.get("match_ratio") or 0.0)
    coverage = float(located.get("coverage") or 0.0)
    return max(ratio, coverage)


def _page_width(page: Any) -> float:
    return float(getattr(getattr(page, "rect", None), "width", 0.0) or 0.0)


def _page_height(page: Any) -> float:
    return float(getattr(getattr(page, "rect", None), "height", 0.0) or 0.0)


def _located_result(
    located: dict[str, Any],
    *,
    document_id: int,
    chunk_id: int,
    pdf_page: int,
    page_index: int,
    page_width: float,
    page_height: float,
    page_text_length: int | None = None,
    warnings: list[str] | None = None,
    original_pdf_page: int | None = None,
    corrected_pdf_page: int | None = None,
    page_metadata_mismatch: bool = False,
) -> PdfChunkLocatorResult:
    match_method = located.get("match_method", "exact_search")
    alignment_status = located.get("alignment_status")
    if alignment_status == "chunk_aligned":
        loc_status = "chunk_aligned"
        loc_reason = "已定位到证据片段主体区域。"
        confidence = located.get("confidence", "medium")
    elif alignment_status == "partial_chunk_aligned":
        loc_status = "partial_chunk_aligned"
        loc_reason = "已定位到证据片段附近区域，部分文本匹配。"
        confidence = "low"
    elif alignment_status == "fallback_term_found":
        loc_status = "fallback_term_found"
        if match_method == "fallback_chunk_text_anchor":
            loc_reason = "该页无可搜索文本层；已根据搜索词在对应页面标出可能区域。"
        else:
            loc_reason = "已根据搜索词在 PDF 页面中找到高亮位置。"
        confidence = "low"
    elif match_method == "fallback_term_search":
        loc_status = "fallback_term_found"
        loc_reason = "已根据搜索词在 PDF 页面中找到高亮位置。"
        confidence = "low"
    elif match_method == "chunk_alignment":
        loc_status = "chunk_aligned"
        loc_reason = "已定位到证据片段。"
        confidence = located.get("confidence", "medium")
    else:
        loc_status = "exact_text_location"
        loc_reason = "已在 PDF 页面中找到精确文本位置。"
        confidence = located.get("confidence", "high")
    if page_metadata_mismatch:
        loc_reason = f"{loc_reason} 已自动定位到相邻页。"
    return _result(
        status="located",
        locator_status=loc_status,
        locator_reason=loc_reason,
        document_id=document_id,
        chunk_id=chunk_id,
        pdf_page=pdf_page,
        page_index=page_index,
        match_method=match_method,
        confidence=confidence,
        rects=located["rects"],
        page_width=page_width,
        page_height=page_height,
        snippet_used=located["snippet_used"],
        matched_term=located.get("matched_term"),
        warnings=warnings,
        original_pdf_page=original_pdf_page,
        corrected_pdf_page=corrected_pdf_page,
        page_metadata_mismatch=page_metadata_mismatch,
        visual_mode=_visual_mode_for_located(located),
        is_exact_text_highlight=_is_exact_text_highlight(located),
        is_layout_text_highlight=False,
        approximate_region=_approximate_region_for_located(located),
        page_text_length=page_text_length,
    )


def _layout_result(
    located: dict[str, Any],
    *,
    document_id: int,
    chunk_id: int,
    snippet: str,
) -> PdfChunkLocatorResult:
    locator_status = str(located.get("locator_status") or "layout_block_location")
    visual_mode = str(located.get("visual_mode") or "layout_block_highlight")
    if locator_status == "layout_line_location":
        reason = "已按书籍版面行定位。"
        method = "layout_line_match"
    elif locator_status == "layout_sentence_location":
        reason = "已按书籍版面句子定位。"
        method = "layout_sentence_match"
    else:
        reason = "已按书籍版面块定位。"
        method = "layout_block_match"
    return _result(
        status="located",
        locator_status=locator_status,
        locator_reason=reason,
        document_id=document_id,
        chunk_id=chunk_id,
        pdf_page=int(located["pdf_page"]),
        page_index=int(located["pdf_page"]) - 1,
        match_method=str(located.get("match_method") or method),
        confidence=str(located.get("confidence") or "medium"),
        rects=list(located.get("rects") or []),
        page_width=located.get("page_width"),
        page_height=located.get("page_height"),
        snippet_used=located.get("snippet_used") or snippet,
        warnings=[],
        visual_mode=visual_mode,
        is_exact_text_highlight=False,
        is_layout_text_highlight=True,
        page_text_length=located.get("page_text_length"),
        matched_lines=list(located.get("matched_lines") or []),
    )


def _candidate_snippets(text: str) -> list[str]:
    normalized = normalize_locator_text(text)
    if not normalized:
        return []
    candidates = [normalized]
    for sentence in _punctuation_boundary_candidates(normalized):
        if sentence and sentence not in candidates:
            candidates.append(sentence)
    for length in (160, 120, 80):
        shortened = _trim_to_word_boundary(normalized, length)
        if shortened and shortened not in candidates:
            candidates.append(shortened)
    return candidates


def _fallback_search_terms(text: str, fallback_terms: list[str] | None = None, limit: int = 8) -> list[str]:
    terms: list[str] = []
    for value in fallback_terms or []:
        _append_unique_term(terms, value)
        for child in _split_fallback_term(value):
            _append_unique_term(terms, child)
    normalized = normalize_locator_text(text)
    for match in re.findall(r"[\u4e00-\u9fff]{2,12}", normalized):
        _append_unique_term(terms, match)
        if len(terms) >= limit:
            return terms
    for match in re.findall(r"\b[A-Z][A-Za-z]+(?:-[A-Z][A-Za-z]+)*\b|\b[A-Z]{2,}\b", normalized):
        _append_unique_term(terms, match)
        if len(terms) >= limit:
            return terms
    for match in re.findall(r"\b[A-Za-z][A-Za-z-]{4,}\b", normalized):
        _append_unique_term(terms, match)
        if len(terms) >= limit:
            return terms
    return terms[:limit]


def _split_fallback_term(value: Any) -> list[str]:
    text = normalize_locator_text(str(value or ""))
    if not text:
        return []
    terms: list[str] = []
    for token in re.split(r"[\s,;，；、/]+", text):
        if len(token) >= 2:
            terms.append(token)
        if re.search(r"[\u4e00-\u9fff]", token):
            for suffix in ("最短路径", "路径"):
                if suffix in token and suffix != token:
                    terms.append(suffix)
    return terms


def _append_unique_term(terms: list[str], value: Any) -> None:
    term = normalize_locator_text(str(value or ""))
    if len(term) < 2:
        return
    folded = term.casefold()
    if any(existing.casefold() == folded for existing in terms):
        return
    terms.append(term)


def _punctuation_boundary_candidates(text: str) -> list[str]:
    matches = list(re.finditer(r"[.!?。！？；;:：]", text))
    candidates: list[str] = []
    for match in matches[:3]:
        candidate = text[: match.end()].strip()
        if 20 <= len(candidate) <= SNIPPET_LIMIT:
            candidates.append(candidate)
    return candidates


def _search_for(page: Any, text: str) -> list[dict[str, float]]:
    if not text:
        return []
    try:
        rects = page.search_for(text)
    except Exception:
        return []
    return [
        {"x0": float(rect.x0), "y0": float(rect.y0), "x1": float(rect.x1), "y1": float(rect.y1)}
        for rect in rects[:8]
    ]


def _search_for_term_variants(page: Any, text: str) -> tuple[list[dict[str, float]], str]:
    term = normalize_locator_text(text)
    if not term:
        return [], term
    variants = [term]
    if re.fullmatch(r"[A-Za-z][A-Za-z-]+", term):
        variants.extend([term.lower(), term.upper(), term.title(), term.replace("-", " ")])
    for variant in _ordered_unique(variants):
        rects = _search_for(page, variant)
        if rects:
            return rects, variant
    return [], term


def _page_has_extractable_text(page: Any) -> bool:
    try:
        return bool(normalize_locator_text(page.get_text("text")))
    except Exception:
        return False


def _fallback_term_in_chunk_text(chunk_text: str, fallback_terms: list[str] | None = None) -> str | None:
    haystack = normalize_locator_text(chunk_text).casefold()
    for term in _fallback_search_terms(chunk_text, fallback_terms=fallback_terms, limit=16):
        normalized = normalize_locator_text(term)
        if len(normalized) < 2:
            continue
        if normalized.casefold() in haystack:
            return normalized
    return None


def _page_level_fallback_rect(page: Any) -> list[dict[str, float]]:
    rect = getattr(page, "rect", None)
    width = float(getattr(rect, "width", 0.0) or 0.0)
    height = float(getattr(rect, "height", 0.0) or 0.0)
    if width <= 0 or height <= 0:
        return []
    return [
        _clamp_rect(
            {
                "x0": width * 0.07,
                "y0": height * 0.08,
                "x1": width * 0.93,
                "y1": height * 0.58,
            },
            width=width,
            height=height,
        )
    ]


def _clamp_rect(rect: dict[str, float], *, width: float, height: float) -> dict[str, float]:
    return {
        "x0": max(0.0, min(width, float(rect.get("x0", 0.0)))),
        "y0": max(0.0, min(height, float(rect.get("y0", 0.0)))),
        "x1": max(0.0, min(width, float(rect.get("x1", 0.0)))),
        "y1": max(0.0, min(height, float(rect.get("y1", 0.0)))),
    }


def _visual_mode_for_located(located: dict[str, Any]) -> str:
    if located.get("visual_mode"):
        return str(located["visual_mode"])
    if located.get("match_method") == "fallback_chunk_text_anchor":
        return "approximate_chunk_region"
    if located.get("rects"):
        return "text_highlight"
    return "page_only"


def _is_exact_text_highlight(located: dict[str, Any]) -> bool:
    if located.get("is_exact_text_highlight") is not None:
        return bool(located.get("is_exact_text_highlight"))
    return located.get("match_method") in {"exact_search", "short_snippet_search", "fallback_term_search", "chunk_alignment"}


def _approximate_region_for_located(located: dict[str, Any]) -> dict[str, float] | None:
    if _visual_mode_for_located(located) != "approximate_chunk_region":
        return None
    rects = located.get("rects") or []
    return dict(rects[0]) if rects else None


def _load_layout_location(document_id: int, chunk_id: int) -> dict[str, Any] | None:
    try:
        with sqlite3.connect(DEFAULT_DB_PATH) as connection:
            return load_layout_location_for_chunk(connection, document_id=document_id, chunk_id=chunk_id)
    except sqlite3.Error:
        return None


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _trim_to_word_boundary(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    shortened = text[:max_chars].rstrip()
    shortened = re.sub(r"\s+\S*$", "", shortened).strip()
    return shortened or text[:max_chars].rstrip()


def _cap_snippet(text: str | None) -> str:
    normalized = normalize_locator_text(text or "")
    if len(normalized) <= SNIPPET_LIMIT:
        return normalized
    return normalized[: SNIPPET_LIMIT - 3].rstrip() + "..."


def _result(
    *,
    status: str,
    document_id: int | None,
    chunk_id: int,
    locator_status: str | None = None,
    locator_reason: str | None = None,
    is_metadata_chunk: bool = False,
    is_locatable: bool | None = None,
    pdf_page: int | None = None,
    page_index: int | None = None,
    match_method: str = "not_found",
    confidence: str = "none",
    rects: list[dict[str, float]] | None = None,
    page_width: float | None = None,
    page_height: float | None = None,
    snippet_used: str = "",
    warnings: list[str] | None = None,
    matched_term: str | None = None,
    matched_lines: list[str] | None = None,
    original_pdf_page: int | None = None,
    corrected_pdf_page: int | None = None,
    page_metadata_mismatch: bool = False,
    visual_mode: str | None = None,
    is_exact_text_highlight: bool | None = None,
    is_layout_text_highlight: bool | None = None,
    approximate_region: dict[str, float] | None = None,
    page_text_length: int | None = None,
) -> PdfChunkLocatorResult:
    resolved_locator_status = locator_status or _locator_status_for(status, rects or [])
    resolved_visual_mode = visual_mode or _visual_mode_for_result(resolved_locator_status, match_method, rects or [])
    resolved_exact_flag = (
        bool(is_exact_text_highlight)
        if is_exact_text_highlight is not None
        else resolved_visual_mode == "text_highlight" and match_method in {"exact_search", "short_snippet_search", "fallback_term_search", "chunk_alignment"}
    )
    resolved_approximate_region = approximate_region
    if resolved_visual_mode == "approximate_chunk_region" and resolved_approximate_region is None and rects:
        resolved_approximate_region = dict(rects[0])
    resolved_is_locatable = (
        is_locatable
        if is_locatable is not None
        else resolved_locator_status
        in {
            "exact_text_location",
            "layout_line_location",
            "layout_sentence_location",
            "layout_block_location",
            "layout_bbox_location",
            "chunk_aligned",
            "partial_chunk_aligned",
            "page_level_only",
        }
    )
    return PdfChunkLocatorResult(
        status=status,
        locator_status=resolved_locator_status,
        locator_reason=locator_reason or _locator_reason_for(resolved_locator_status),
        is_metadata_chunk=is_metadata_chunk,
        is_locatable=bool(resolved_is_locatable),
        document_id=document_id,
        chunk_id=chunk_id,
        pdf_page=pdf_page,
        page_index=page_index,
        match_method=match_method,
        confidence=confidence,
        rects=rects or [],
        page_width=page_width,
        page_height=page_height,
        snippet_used=_cap_snippet(snippet_used),
        warnings=warnings or [],
        highlight_count=len(rects or []),
        matched_term=matched_term,
        matched_lines=matched_lines or [],
        original_pdf_page=original_pdf_page,
        corrected_pdf_page=corrected_pdf_page,
        page_metadata_mismatch=page_metadata_mismatch,
        visual_mode=resolved_visual_mode,
        is_exact_text_highlight=resolved_exact_flag,
        is_layout_text_highlight=bool(is_layout_text_highlight),
        approximate_region=resolved_approximate_region,
        page_text_length=page_text_length,
    )


def _visual_mode_for_result(locator_status: str, match_method: str, rects: list[dict[str, float]]) -> str:
    if match_method == "fallback_chunk_text_anchor":
        return "approximate_chunk_region"
    if locator_status in {"layout_block_location", "layout_bbox_location"}:
        return "layout_block_highlight"
    if locator_status in {"layout_line_location", "layout_sentence_location"}:
        return "layout_line_highlight"
    if rects:
        return "text_highlight"
    if locator_status == "page_level_only":
        return "page_only"
    return "none"


def _locator_status_for(status: str, rects: list[dict[str, float]]) -> str:
    if status == "located" and rects:
        return "exact_text_location"
    if status == "fallback_term_found":
        return "fallback_term_found"
    if status in {"page_level_only", "not_found"}:
        return "page_level_only"
    if status in {"chunk_aligned", "partial_chunk_aligned", "fallback_term_found"}:
        return status
    if status in {"no_page", "page_unavailable", "dependency_unavailable"}:
        return "no_page"
    if status in {"no_text", "metadata_non_locatable", "pdf_missing"}:
        return status
    if status == "pdf_unavailable":
        return "pdf_missing"
    return "not_found"


def _locator_reason_for(locator_status: str) -> str:
    return {
        "exact_text_location": "已在 PDF 页面中找到精确文本位置。",
        "layout_line_location": "已按书籍版面行定位。",
        "layout_sentence_location": "已按书籍版面句子定位。",
        "layout_block_location": "已按书籍版面块定位。",
        "layout_bbox_location": "已按书籍版面块定位。",
        "chunk_aligned": "已定位到证据片段主体区域。",
        "partial_chunk_aligned": "已定位到证据片段附近区域，部分文本匹配。",
        "fallback_term_found": "已根据搜索词在 PDF 页面中找到高亮位置。",
        "page_level_only": "页码定位成功，精确文本坐标未找到；已显示对应页面。",
        "no_page": "该片段缺少 PDF 页码，只能打开文档。",
        "no_text": "该片段缺少正文文本，无法定位。",
        "metadata_non_locatable": "该片段是抽取元信息，不支持 PDF 定位。",
        "pdf_missing": "该片段缺少可预览 PDF，只能查看证据文本。",
        "not_found": "未能定位该片段。",
    }.get(locator_status, "未能定位该片段。")
