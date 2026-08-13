from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from app.runtime.contracts import RuntimeState, RuntimeStatus
from app.runtime.health import HealthResult
from app.runtime.model_readiness import (
    configure_model_readiness,
    mark_model_failed,
    mark_model_loading,
    mark_model_ready,
    public_model_readiness,
    reset_model_readiness_for_tests,
    set_api_ready,
)
from app.runtime.supervisor import _apply_backend_readiness
from app.services import local_embedding_service, local_reranker_service


class _Array(list[float]):
    def tolist(self) -> list[float]:
        return list(self)


@pytest.fixture(autouse=True)
def _isolated_model_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SEARCH_LOG_DIR", str(tmp_path / "logs"))
    reset_model_readiness_for_tests()
    local_embedding_service.shutdown_embedding_model()
    local_reranker_service.shutdown_reranker_model()
    yield
    local_embedding_service.shutdown_embedding_model()
    local_reranker_service.shutdown_reranker_model()
    reset_model_readiness_for_tests()


def test_api_ready_while_models_are_loading_is_not_retrieval_ready() -> None:
    configure_model_readiness(configured=True)
    set_api_ready(True)
    mark_model_loading("embedding")
    state = public_model_readiness()
    assert state["api_ready"] is True
    assert state["model_state"] == "loading"
    assert state["retrieval_ready"] is False


def test_embedding_ready_reranker_loading_is_not_retrieval_ready() -> None:
    configure_model_readiness(configured=True)
    set_api_ready(True)
    mark_model_ready("embedding")
    mark_model_loading("reranker")
    state = public_model_readiness()
    assert state["embedding_state"] == "ready"
    assert state["reranker_state"] == "loading"
    assert state["retrieval_ready"] is False


def test_both_self_checked_models_are_retrieval_ready() -> None:
    configure_model_readiness(configured=True)
    mark_model_ready("embedding")
    mark_model_ready("reranker")
    set_api_ready(True)
    state = public_model_readiness()
    assert state["model_state"] == "ready"
    assert state["retrieval_ready"] is True


def test_model_initialization_failure_is_stable_and_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSentenceTransformer:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("private native failure")

    _prepare_embedding(monkeypatch, FailingSentenceTransformer)
    with pytest.raises(
        local_embedding_service.LocalEmbeddingUnavailable,
        match="embedding_model_load_failed",
    ):
        local_embedding_service.initialize_embedding_model()
    state = public_model_readiness()
    assert state["embedding_state"] == "failed"
    assert state["last_model_error_code"] == "embedding_model_load_failed"
    assert state["retrieval_ready"] is False


def test_embedding_self_check_rejects_non_finite_or_wrong_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WrongDimensionModel:
        def __init__(self, *_args, **_kwargs):
            pass

        def encode(self, *_args, **_kwargs):
            return [_Array([1.0, 2.0])]

    _prepare_embedding(monkeypatch, WrongDimensionModel)
    with pytest.raises(
        local_embedding_service.LocalEmbeddingUnavailable,
        match="embedding_model_self_check_failed",
    ):
        local_embedding_service.initialize_embedding_model()
    assert public_model_readiness()["embedding_state"] == "failed"


def test_ready_model_inference_exception_transitions_to_failed() -> None:
    class BrokenModel:
        def encode(self, *_args, **_kwargs):
            raise RuntimeError("native inference failed")

    configure_model_readiness(configured=True)
    mark_model_ready("embedding")
    with pytest.raises(
        local_embedding_service.LocalEmbeddingUnavailable,
        match="embedding_model_inference_failed",
    ):
        local_embedding_service._encode_text(BrokenModel(), "probe")
    state = public_model_readiness()
    assert state["embedding_state"] == "failed"
    assert state["last_model_error_code"] == "embedding_model_inference_failed"


def test_runtime_observes_ready_to_failed_without_stale_booleans() -> None:
    status = RuntimeStatus(state=RuntimeState.LOCAL_READY_TUNNEL_MISSING, updated_at="now")
    _apply_backend_readiness(
        status,
        HealthResult(
            True,
            details={
                "api_ready": True,
                "retrieval_ready": True,
                "model_state": "ready",
                "embedding_state": "ready",
                "reranker_state": "ready",
                "last_model_error_code": None,
                "last_state_change": "2026-07-24T00:00:00Z",
            },
        ),
    )
    assert status.embedding_model_ready is True
    assert status.reranker_model_ready is True

    _apply_backend_readiness(
        status,
        HealthResult(
            False,
            "backend_retrieval_not_ready",
            details={
                "api_ready": True,
                "retrieval_ready": False,
                "model_state": "failed",
                "embedding_state": "failed",
                "reranker_state": "ready",
                "last_model_error_code": "embedding_model_inference_failed",
                "last_state_change": "2026-07-24T00:01:00Z",
            },
        ),
    )
    assert status.retrieval_ready is False
    assert status.embedding_model_ready is False
    assert status.reranker_model_ready is True
    assert status.last_model_error_code == "embedding_model_inference_failed"


def test_concurrent_first_embedding_requests_construct_exactly_one_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = Event()
    release = Event()
    counter_lock = Lock()
    constructor_count = 0

    class SerializedSentenceTransformer:
        def __init__(self, *_args, **_kwargs):
            nonlocal constructor_count
            with counter_lock:
                constructor_count += 1
            entered.set()
            assert release.wait(timeout=5)

        def encode(self, *_args, **_kwargs):
            return [_Array([0.001] * 1024)]

    _prepare_embedding(monkeypatch, SerializedSentenceTransformer)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(local_embedding_service.initialize_embedding_model)
        assert entered.wait(timeout=5)
        second = executor.submit(local_embedding_service.initialize_embedding_model)
        release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert constructor_count == 1
    assert public_model_readiness()["embedding_state"] == "ready"


def test_reranker_self_check_requires_one_finite_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidReranker:
        def __init__(self, *_args, **_kwargs):
            pass

        def predict(self, *_args, **_kwargs):
            return []

    _prepare_reranker(monkeypatch, InvalidReranker)
    with pytest.raises(
        local_reranker_service.LocalRerankerUnavailable,
        match="reranker_model_self_check_failed",
    ):
        local_reranker_service.initialize_reranker_model()
    assert public_model_readiness()["reranker_state"] == "failed"


def test_graceful_shutdown_then_cold_restart_reinitializes_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor_count = 0

    class CountingSentenceTransformer:
        def __init__(self, *_args, **_kwargs):
            nonlocal constructor_count
            constructor_count += 1

        def encode(self, *_args, **_kwargs):
            return [_Array([0.001] * 1024)]

    _prepare_embedding(monkeypatch, CountingSentenceTransformer)
    local_embedding_service.initialize_embedding_model()
    local_embedding_service.shutdown_embedding_model()
    reset_model_readiness_for_tests()
    configure_model_readiness(configured=True)
    local_embedding_service.initialize_embedding_model()
    assert constructor_count == 2
    assert public_model_readiness()["embedding_state"] == "ready"


def _prepare_embedding(
    monkeypatch: pytest.MonkeyPatch,
    sentence_transformer: type,
) -> None:
    configure_model_readiness(configured=True)
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=sentence_transformer),
    )
    monkeypatch.setattr(local_embedding_service, "_set_local_cache_env", lambda: None)
    monkeypatch.setattr(local_embedding_service, "_model_path", lambda: Path("D:/isolated/model"))
    monkeypatch.setattr(local_embedding_service, "_device_name", lambda: "cpu")


def _prepare_reranker(
    monkeypatch: pytest.MonkeyPatch,
    cross_encoder: type,
) -> None:
    configure_model_readiness(configured=True)
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(CrossEncoder=cross_encoder),
    )
    monkeypatch.setattr(local_reranker_service, "_set_local_cache_env", lambda: None)
    monkeypatch.setattr(local_reranker_service, "_model_path", lambda: Path("D:/isolated/model"))
    monkeypatch.setattr(local_reranker_service, "_device_name", lambda: "cpu")
