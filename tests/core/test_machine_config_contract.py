from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.runtime.machine_config import (
    MachineConfigUnavailable,
    load_machine_config,
    write_machine_config,
)
from app.runtime.config import RuntimeConfig
from app.runtime.process_manager import ProcessStartError
from app.runtime.model_readiness import reset_model_readiness_for_tests
from app.runtime.supervisor import RuntimeController, RuntimeSupervisor
from app.api.product_api import health
from app.services import high_quality_search_service


@pytest.fixture(autouse=True)
def _reset_model_state() -> None:
    reset_model_readiness_for_tests()
    yield
    reset_model_readiness_for_tests()


def _write_model(path: Path, *, reranker: bool) -> Path:
    path.mkdir(parents=True)
    (path / "config.json").write_text(
        json.dumps({"model_type": "qwen3", "hidden_size": 1024}), encoding="utf-8"
    )
    modules = (
        [{"type": "sentence_transformers.cross_encoder.modules.logit_score.LogitScore"}]
        if reranker
        else [
            {"type": "sentence_transformers.models.Pooling"},
            {"type": "sentence_transformers.models.Normalize"},
        ]
    )
    (path / "modules.json").write_text(json.dumps(modules), encoding="utf-8")
    (path / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (path / "model.safetensors").write_bytes(b"fixture")
    if reranker:
        (path / "config_sentence_transformers.json").write_text(
            json.dumps({"model_type": "CrossEncoder"}), encoding="utf-8"
        )
    return path


def _valid_paths(tmp_path: Path) -> tuple[Path, Path]:
    embedding = _write_model(tmp_path / "中文 model" / "Qwen3-Embedding-0.6B", reranker=False)
    reranker = _write_model(tmp_path / "中文 model" / "Qwen3-Reranker-0.6B", reranker=True)
    return embedding, reranker


@pytest.mark.parametrize(
    ("content", "status"),
    [
        ("", "config_invalid_json"),
        ("{", "config_invalid_json"),
        ("[]", "config_invalid_json"),
        ('{"schema_version":2}', "schema_unsupported"),
        ('{"schema_version":1}', "required_field_missing"),
    ],
)
def test_machine_config_rejects_invalid_documents(tmp_path: Path, content: str, status: str) -> None:
    path = tmp_path / "machine-config.json"
    path.write_text(content, encoding="utf-8")
    assert load_machine_config(path).status == status


def test_machine_config_missing_relative_and_missing_model_states(tmp_path: Path) -> None:
    assert load_machine_config(tmp_path / "missing.json").status == "config_missing"
    assert load_machine_config("relative.json").status == "model_path_not_absolute"
    path = tmp_path / "machine-config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "high_quality_search": {
                    "embedding_model_path": "relative",
                    "reranker_model_path": str(tmp_path / "missing"),
                },
            }
        ),
        encoding="utf-8",
    )
    assert load_machine_config(path).status == "model_path_not_absolute"


def test_machine_config_validates_real_roles_and_redacts_paths(tmp_path: Path) -> None:
    embedding, reranker = _valid_paths(tmp_path)
    config_path = tmp_path / "user data" / "machine-config.json"
    config = write_machine_config(
        config_path,
        embedding_model_path=f"{embedding}\\",
        reranker_model_path=f"{reranker}\\",
    )
    assert config.ready is True
    public = config.public_status()
    serialized = json.dumps(public, ensure_ascii=False)
    assert public["embedding_model"]["name"] == "Qwen3-Embedding-0.6B"
    assert public["reranker_model"]["name"] == "Qwen3-Reranker-0.6B"
    assert str(tmp_path) not in serialized
    assert len(public["embedding_model"]["path_hash"]) == 64


def test_machine_config_structure_and_atomic_backup_contract(tmp_path: Path) -> None:
    embedding, reranker = _valid_paths(tmp_path)
    config_path = tmp_path / "machine-config.json"
    first = write_machine_config(
        config_path,
        embedding_model_path=embedding,
        reranker_model_path=reranker,
    )
    assert first.status == "model_ready"
    original = config_path.read_bytes()
    write_machine_config(
        config_path,
        embedding_model_path=embedding,
        reranker_model_path=reranker,
    )
    assert config_path.with_name("machine-config.json.bak").read_bytes() == original
    (embedding / "modules.json").write_text("[]", encoding="utf-8")
    with pytest.raises(MachineConfigUnavailable, match="model_structure_invalid"):
        write_machine_config(
            config_path,
            embedding_model_path=embedding,
            reranker_model_path=reranker,
        )
    assert config_path.read_bytes() == original


def test_runtime_passes_one_explicit_machine_config_to_all_children(tmp_path: Path) -> None:
    embedding, reranker = _valid_paths(tmp_path)
    config_path = tmp_path / "roaming" / "Search" / "machine-config.json"
    write_machine_config(config_path, embedding_model_path=embedding, reranker_model_path=reranker)
    runtime_root = tmp_path / "runtime-project"
    runtime_root.mkdir()
    config = RuntimeConfig.load(
        runtime_root=runtime_root,
        machine_config_path=config_path,
        env={
            "LOCALAPPDATA": str(tmp_path / "local"),
            "APPDATA": str(tmp_path / "roaming"),
            "SEARCH_PYTHON": str(tmp_path / "python.exe"),
            "SEARCH_NODE": str(tmp_path / "node.exe"),
        },
    )
    supervisor = RuntimeSupervisor(config)
    assert supervisor.status.machine_config_status == "model_ready"
    assert supervisor.status.embedding_model_ready is False
    assert supervisor.status.reranker_model_ready is False
    assert supervisor.status.model_state == "unconfigured"
    expected = str(config_path.resolve())
    assert supervisor._fastapi_spec().environment["SEARCH_MACHINE_CONFIG_PATH"] == expected
    assert supervisor._mcp_spec().environment["SEARCH_MACHINE_CONFIG_PATH"] == expected
    captured = []
    controller = RuntimeController(config)
    def capture_and_fail(spec):
        captured.append(spec)
        raise ProcessStartError("fixture")

    controller.process_manager.spawn = capture_and_fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="supervisor_start_failed"):
        controller.start(wait_seconds=0)
    assert captured
    arguments = captured[0].arguments
    assert arguments[2:4] == ("--machine-config", expected)
    assert arguments[4] == "supervise"


def test_high_quality_search_is_explicitly_unavailable_without_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEARCH_MACHINE_CONFIG_PATH", raising=False)
    with pytest.raises(MachineConfigUnavailable, match="config_missing"):
        high_quality_search_service.search_high_quality("query")


def test_health_machine_config_status_never_exposes_absolute_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding, reranker = _valid_paths(tmp_path)
    config_path = tmp_path / "machine-config.json"
    write_machine_config(config_path, embedding_model_path=embedding, reranker_model_path=reranker)
    monkeypatch.setenv("SEARCH_MACHINE_CONFIG_PATH", str(config_path))
    payload = health()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["machine_config"]["status"] == "model_loading"
    assert payload["retrieval_ready"] is False
    assert str(tmp_path) not in serialized
    assert "machine-config.json" not in serialized
