from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas.evidence_export import EvidenceExportRequest, EvidenceExportResponse
from app.schemas.retrieval_selection import (
    RetrievalSelectionResponse,
    RetrievalSelectionSelector,
)
from app.services.retrieval.evidence_errors import EvidenceWorkflowError
from app.services.retrieval.evidence_export_service import export_evidence
from app.services.retrieval.evidence_selection_service import resolve_selection


router = APIRouter(prefix="/api/v1/retrieval", tags=["local-retrieval-evidence"])


@router.post("/selection/resolve", response_model=RetrievalSelectionResponse)
def resolve_retrieval_selection(
    selector: RetrievalSelectionSelector,
) -> dict[str, Any]:
    try:
        return resolve_selection(selector)
    except EvidenceWorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_detail()) from exc


@router.post("/evidence/export", response_model=EvidenceExportResponse)
def export_retrieval_evidence(
    request: EvidenceExportRequest,
) -> dict[str, Any]:
    try:
        return export_evidence(request)
    except EvidenceWorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_detail()) from exc
