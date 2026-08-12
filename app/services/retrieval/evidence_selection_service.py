from __future__ import annotations

from pathlib import Path
from typing import Any

from app.schemas.retrieval_selection import (
    DocumentScopeSelection,
    ExplicitSelection,
    EvidenceBasketItem,
    RetrievalSelectionResponse,
    RetrievalSelectionSelector,
    SearchResultsSelection,
)
from app.services.retrieval.evidence_errors import EvidenceWorkflowError
from app.services.retrieval.evidence_loader import (
    EvidenceRecord,
    load_document_fragment_ids,
    load_evidence_records,
)
from app.services.retrieval.fts_search_service import search_retrieval


MAX_EXPLICIT_ITEMS = 1000
SEARCH_PAGE_SIZE = 200


def resolve_selection(
    selector: RetrievalSelectionSelector,
    *,
    index_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    if isinstance(selector, ExplicitSelection):
        fragment_ids, duplicate_count = _unique_ids(selector.fragment_ids)
        if duplicate_count:
            warnings.append(f"duplicate_fragment_ids_removed:{duplicate_count}")
        if len(fragment_ids) > MAX_EXPLICIT_ITEMS:
            raise _limit_error(len(fragment_ids), MAX_EXPLICIT_ITEMS, "explicit")
        selection_type = "explicit"
        load_result = load_evidence_records(
            fragment_ids,
            index_path=index_path,
            manifest_path=manifest_path,
        )
    elif isinstance(selector, SearchResultsSelection):
        fragment_ids = _resolve_search_result_ids(
            selector,
            index_path=index_path,
            manifest_path=manifest_path,
        )
        selection_type = "search_results"
        if not fragment_ids:
            warnings.append("search_returned_no_results")
        load_result = load_evidence_records(
            fragment_ids,
            index_path=index_path,
            manifest_path=manifest_path,
        )
    elif isinstance(selector, DocumentScopeSelection):
        fragment_ids, total, status = load_document_fragment_ids(
            selector.document_id,
            list(selector.source_types),
            index_path=index_path,
            manifest_path=manifest_path,
            fetch_limit=selector.max_items + 1,
        )
        if total > selector.max_items:
            raise _limit_error(total, selector.max_items, "document_scope")
        selection_type = "document_scope"
        if not fragment_ids:
            warnings.append("document_scope_returned_no_results")
        load_result = load_evidence_records(
            fragment_ids,
            index_path=index_path,
            manifest_path=manifest_path,
        )
        if load_result.index_status["index_content_hash"] != status["index_content_hash"]:
            raise EvidenceWorkflowError(
                "retrieval_index_changed_during_selection",
                "The retrieval index changed while resolving the document selection.",
                status_code=409,
            )
    else:
        raise EvidenceWorkflowError(
            "unsupported_selection_type",
            "Unsupported retrieval selection type.",
            status_code=400,
        )

    items = [
        _basket_item(record, selected_order=index + 1)
        for index, record in enumerate(load_result.records)
    ]
    response = {
        "status": "OK",
        "selection_type": selection_type,
        "resolved_fragment_ids": [item.fragment_id for item in load_result.records],
        "resolved_count": len(load_result.records),
        "items": items,
        "warnings": warnings,
        "source_index_hash": load_result.index_status["index_content_hash"],
        "source_manifest_hash": load_result.index_status["manifest_sha256"],
        "db_write_performed": False,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_write_performed": False,
        "llm_called": False,
    }
    return RetrievalSelectionResponse.model_validate(response).model_dump(mode="json")


def _resolve_search_result_ids(
    selector: SearchResultsSelection,
    *,
    index_path: str | Path | None,
    manifest_path: str | Path | None,
) -> list[str]:
    base_request = selector.search_request.model_copy(
        update={"limit": SEARCH_PAGE_SIZE, "offset": 0}
    )
    first = search_retrieval(
        base_request,
        index_path=index_path,
        manifest_path=manifest_path,
    )
    available = int(first["counts"]["ranked_candidates"])
    if available > selector.max_items:
        raise _limit_error(available, selector.max_items, "search_results")
    fragment_ids = [str(item["fragment_id"]) for item in first["results"]]
    offset = len(fragment_ids)
    while offset < available:
        page_request = base_request.model_copy(update={"offset": offset})
        page = search_retrieval(
            page_request,
            index_path=index_path,
            manifest_path=manifest_path,
        )
        page_ids = [str(item["fragment_id"]) for item in page["results"]]
        if not page_ids:
            raise EvidenceWorkflowError(
                "search_selection_resolution_incomplete",
                "Search pagination ended before all ranked results were resolved.",
                status_code=409,
                details={"resolved_count": len(fragment_ids), "expected_count": available},
            )
        fragment_ids.extend(page_ids)
        offset += len(page_ids)
    unique, _ = _unique_ids(fragment_ids)
    return unique


def _unique_ids(values: list[str]) -> tuple[list[str], int]:
    result: list[str] = []
    seen: set[str] = set()
    duplicate_count = 0
    for value in values:
        fragment_id = str(value).strip()
        if not fragment_id:
            continue
        if fragment_id in seen:
            duplicate_count += 1
            continue
        seen.add(fragment_id)
        result.append(fragment_id)
    return result, duplicate_count


def _limit_error(available: int, limit: int, selection_type: str) -> EvidenceWorkflowError:
    return EvidenceWorkflowError(
        "selection_limit_exceeded",
        f"Selection contains {available} fragments, exceeding the cap of {limit}.",
        status_code=413,
        details={
            "selection_type": selection_type,
            "available_count": available,
            "max_items": limit,
        },
    )


def _basket_item(
    record: EvidenceRecord,
    *,
    selected_order: int,
) -> EvidenceBasketItem:
    return EvidenceBasketItem(
        fragment_id=record.fragment_id,
        display_id=record.display_id,
        source_type=record.source_type,
        origin_kind=record.origin_kind,
        document_id=record.document_id,
        title=record.title,
        authors=record.authors,
        year=record.year,
        page_number=record.page_number,
        page_label=record.page_label,
        section=record.section,
        selected_order=selected_order,
        duplicate_count=record.duplicate_count,
        warnings=record.warnings,
    )
