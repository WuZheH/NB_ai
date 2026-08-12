from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.runtime.model_readiness import (
    configure_model_readiness,
    mark_model_loading,
    reset_model_readiness_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_readiness() -> None:
    reset_model_readiness_for_tests()
    yield
    reset_model_readiness_for_tests()


def test_unset_search_log_dir_disables_readiness_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEARCH_LOG_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    configure_model_readiness(configured=True)
    mark_model_loading("embedding")
    assert list(tmp_path.rglob("model-readiness.jsonl")) == []


def test_empty_search_log_dir_disables_readiness_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEARCH_LOG_DIR", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    configure_model_readiness(configured=True)
    mark_model_loading("embedding")
    assert list(tmp_path.rglob("model-readiness.jsonl")) == []


def test_local_app_data_never_becomes_an_implicit_readiness_log_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_app_data = tmp_path / "local-app-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("SEARCH_LOG_DIR", raising=False)
    configure_model_readiness(configured=True)
    mark_model_loading("embedding")
    assert not (local_app_data / "Search" / "logs" / "model-readiness.jsonl").exists()
    assert not local_app_data.exists()


def test_explicit_search_log_dir_writes_only_the_readiness_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "explicit-logs"
    monkeypatch.setenv("SEARCH_LOG_DIR", str(log_dir))
    configure_model_readiness(configured=True)
    mark_model_loading("embedding")
    log_path = log_dir / "model-readiness.jsonl"
    assert log_path.is_file()
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["schema"] == "search.model-readiness.v1"
    assert lines[-1]["component"] == "embedding"
    assert lines[-1]["state"] == "loading"
    assert [path.name for path in log_dir.iterdir()] == ["model-readiness.jsonl"]
