from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.runtime.machine_config import MachineConfigUnavailable

from app.domains.retrieval.fragment_locator_service import (
    FragmentLocatorNotFound,
    get_fragment_locator,
)
from app.domains.retrieval.fragment_repository import (
    NotebookFragmentNotFound,
    get_notebook_fragment,
)
from app.domains.retrieval.locator_contracts import FragmentLocator
from app.domains.retrieval.notebook_search_service import (
    NotebookSearchUnavailable,
    search_notebook,
)
from app.domains.retrieval.public_evidence import serialize_public_evidence
from app.domains.retrieval.result_contracts import NotebookSearchResponse, PublicEvidence
from app.schemas.notebook_search import NotebookSearchRequest
from app.schemas.retrieval_search import (
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from app.services import local_embedding_service, local_reranker_service
from app.services.retrieval import fts_search_service, fts_status_service


router = APIRouter(prefix="/api/v1/retrieval", tags=["local-retrieval"])


@router.post("/notebook-search", response_model=NotebookSearchResponse)
def search_notebook_retrieval(
    request: NotebookSearchRequest,
) -> dict[str, Any]:
    try:
        return search_notebook(request)
    except MachineConfigUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "high_quality_search_configuration_unavailable",
                "error_code": exc.error_code,
                "message": "高质量搜索配置不可用。",
                **_safety_flags(),
            },
        ) from exc
    except (
        local_embedding_service.LocalEmbeddingUnavailable,
        local_reranker_service.LocalRerankerUnavailable,
    ) as exc:
        error_code = (
            str(exc)
            if str(exc)
            in {
                "model_load_failed",
                "embedding_model_load_failed",
                "embedding_model_self_check_failed",
                "embedding_model_inference_failed",
                "reranker_model_load_failed",
                "reranker_model_self_check_failed",
                "reranker_model_inference_failed",
            }
            else "model_load_failed"
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "high_quality_search_model_unavailable",
                "error_code": error_code,
                "message": "High-quality search model could not be loaded.",
                **_safety_flags(),
            },
        ) from exc
    except NotebookSearchUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "notebook_search_unavailable",
                "message": "High-quality search is unavailable.",
                **_safety_flags(),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_notebook_search",
                "message": str(exc),
                **_safety_flags(),
            },
        ) from exc


@router.get("/fragments/{fragment_id}", response_model=PublicEvidence)
def fetch_notebook_fragment(fragment_id: str) -> dict[str, Any]:
    try:
        return serialize_public_evidence(
            get_notebook_fragment(fragment_id),
        ).model_dump(mode="json")
    except NotebookFragmentNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "notebook_fragment_not_found",
                "message": str(exc),
                **_safety_flags(),
            },
        ) from exc


@router.get("/fragments/{fragment_id}/locator", response_model=FragmentLocator)
def fetch_fragment_locator(fragment_id: str) -> dict[str, Any]:
    try:
        return get_fragment_locator(fragment_id).model_dump(mode="json")
    except FragmentLocatorNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "fragment_locator_not_found",
                "message": str(exc),
                **_safety_flags(),
            },
        ) from exc


@router.post("/search", response_model=RetrievalSearchResponse)
def search_retrieval(
    request: RetrievalSearchRequest,
) -> dict[str, Any]:
    try:
        return fts_search_service.search_retrieval(request)
    except fts_search_service.RetrievalIndexUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "retrieval_index_unavailable",
                "index_status": exc.status,
                **_safety_flags(),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_retrieval_query",
                "message": str(exc),
                **_safety_flags(),
            },
        ) from exc


@router.get("/index/status", response_model=None)
def retrieval_index_status() -> dict[str, Any]:
    return fts_status_service.get_index_status()


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_write_performed": False,
        "llm_called": False,
    }
