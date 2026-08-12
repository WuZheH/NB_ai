from __future__ import annotations

import inspect

from app.api import library_api
from app.services import (
    high_quality_search_service,
    local_embedding_service,
    local_reranker_service,
    object_semantic_search_service,
)


EXPECTED_DEFAULTS = {
    "object_limit": 50,
    "passage_recall_limit": 30,
    "passage_limit": 15,
}


def test_high_quality_model_identity_is_stable() -> None:
    assert local_embedding_service.MODEL_NAME == "Qwen3-Embedding-0.6B"
    assert local_embedding_service.DEFAULT_MODEL_PATH.name == local_embedding_service.MODEL_NAME
    assert local_reranker_service.RERANKER_MODEL_NAME == "Qwen3-Reranker-0.6B"
    assert (
        local_reranker_service.DEFAULT_RERANKER_MODEL_PATH.name
        == local_reranker_service.RERANKER_MODEL_NAME
    )


def test_high_quality_service_keeps_embedding_and_reranker_wiring() -> None:
    assert high_quality_search_service.local_reranker_service is local_reranker_service
    assert (
        high_quality_search_service.object_semantic_search_service
        is object_semantic_search_service
    )
    assert object_semantic_search_service.local_embedding_service is local_embedding_service
    assert object_semantic_search_service.local_reranker_service is local_reranker_service
    assert local_reranker_service.local_embedding_service is local_embedding_service


def test_high_quality_defaults_match_current_service_and_api_contract() -> None:
    assert high_quality_search_service.DEFAULT_OBJECT_LIMIT == EXPECTED_DEFAULTS["object_limit"]
    assert (
        high_quality_search_service.DEFAULT_PASSAGE_RECALL_LIMIT
        == EXPECTED_DEFAULTS["passage_recall_limit"]
    )
    assert high_quality_search_service.DEFAULT_PASSAGE_LIMIT == EXPECTED_DEFAULTS["passage_limit"]

    service_signature = inspect.signature(high_quality_search_service.search_high_quality)
    api_signature = inspect.signature(library_api.search_high_quality)
    for parameter_name, expected in EXPECTED_DEFAULTS.items():
        assert service_signature.parameters[parameter_name].default == expected
        assert api_signature.parameters[parameter_name].default.default == expected

