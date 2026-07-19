from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


MACHINE_CONFIG_SCHEMA_VERSION = 1
MACHINE_CONFIG_ENV = "SEARCH_MACHINE_CONFIG_PATH"
EMBEDDING_MODEL_NAME = "Qwen3-Embedding-0.6B"
RERANKER_MODEL_NAME = "Qwen3-Reranker-0.6B"
_MODEL_LOAD_FAILURES: set[str] = set()


class MachineConfigUnavailable(RuntimeError):
    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True)
class MachineModelConfig:
    path: Path
    name: str

    @property
    def basename(self) -> str:
        return self.path.name

    @property
    def path_hash(self) -> str:
        normalized = os.path.normcase(str(self.path.resolve(strict=False)))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MachineConfig:
    path: Path | None
    status: str
    error_code: str | None = None
    schema_version: int | None = None
    embedding: MachineModelConfig | None = None
    reranker: MachineModelConfig | None = None

    @property
    def ready(self) -> bool:
        return self.status == "model_ready" and self.embedding is not None and self.reranker is not None

    def require_ready(self) -> "MachineConfig":
        if not self.ready:
            raise MachineConfigUnavailable(self.error_code or self.status)
        return self

    def public_status(self) -> dict[str, Any]:
        load_failed = bool(_MODEL_LOAD_FAILURES) and self.ready
        return {
            "status": "model_load_failed" if load_failed else self.status,
            "error_code": "model_load_failed" if load_failed else self.error_code,
            "configured": self.path is not None and self.path.is_file(),
            "schema_version": self.schema_version,
            "embedding_model": _public_model_status(
                self.embedding, load_failed="embedding" in _MODEL_LOAD_FAILURES
            ),
            "reranker_model": _public_model_status(
                self.reranker, load_failed="reranker" in _MODEL_LOAD_FAILURES
            ),
        }


def default_machine_config_path(*, roaming_app_data: str | Path | None = None) -> Path:
    value = roaming_app_data or os.environ.get("APPDATA")
    if not value:
        raise RuntimeError("search_roaming_app_data_unavailable")
    return Path(value).expanduser().resolve() / "Search" / "machine-config.json"


def load_machine_config(path: str | Path | None) -> MachineConfig:
    if path is None or not str(path).strip():
        return MachineConfig(path=None, status="config_missing", error_code="config_missing")
    candidate = Path(str(path).strip()).expanduser()
    if not candidate.is_absolute():
        return MachineConfig(path=candidate, status="model_path_not_absolute", error_code="model_path_not_absolute")
    candidate = candidate.resolve(strict=False)
    if not candidate.is_file():
        return MachineConfig(path=candidate, status="config_missing", error_code="config_missing")
    try:
        raw = candidate.read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return MachineConfig(path=candidate, status="config_invalid_json", error_code="config_invalid_json")
    if not isinstance(value, dict):
        return MachineConfig(path=candidate, status="config_invalid_json", error_code="config_invalid_json")
    if set(value).difference({"schema_version", "high_quality_search"}):
        return MachineConfig(path=candidate, status="config_invalid_json", error_code="config_invalid_json")
    schema_version = value.get("schema_version")
    if schema_version != MACHINE_CONFIG_SCHEMA_VERSION:
        return MachineConfig(
            path=candidate,
            status="schema_unsupported",
            error_code="schema_unsupported",
            schema_version=schema_version if isinstance(schema_version, int) else None,
        )
    high_quality = value.get("high_quality_search")
    if not isinstance(high_quality, dict):
        return MachineConfig(
            path=candidate,
            status="required_field_missing",
            error_code="required_field_missing",
            schema_version=MACHINE_CONFIG_SCHEMA_VERSION,
        )
    if set(high_quality).difference({"embedding_model_path", "reranker_model_path"}):
        return MachineConfig(
            path=candidate,
            status="config_invalid_json",
            error_code="config_invalid_json",
            schema_version=MACHINE_CONFIG_SCHEMA_VERSION,
        )
    embedding_value = high_quality.get("embedding_model_path")
    reranker_value = high_quality.get("reranker_model_path")
    if not _nonempty_string(embedding_value) or not _nonempty_string(reranker_value):
        return MachineConfig(
            path=candidate,
            status="required_field_missing",
            error_code="required_field_missing",
            schema_version=MACHINE_CONFIG_SCHEMA_VERSION,
        )
    embedding_path = Path(str(embedding_value).strip()).expanduser()
    reranker_path = Path(str(reranker_value).strip()).expanduser()
    if not embedding_path.is_absolute() or not reranker_path.is_absolute():
        return MachineConfig(
            path=candidate,
            status="model_path_not_absolute",
            error_code="model_path_not_absolute",
            schema_version=MACHINE_CONFIG_SCHEMA_VERSION,
        )
    embedding_path = embedding_path.resolve(strict=False)
    reranker_path = reranker_path.resolve(strict=False)
    if not embedding_path.is_dir() or not reranker_path.is_dir():
        return MachineConfig(
            path=candidate,
            status="model_path_not_found",
            error_code="model_path_not_found",
            schema_version=MACHINE_CONFIG_SCHEMA_VERSION,
        )
    if not _valid_embedding_structure(embedding_path) or not _valid_reranker_structure(reranker_path):
        return MachineConfig(
            path=candidate,
            status="model_structure_invalid",
            error_code="model_structure_invalid",
            schema_version=MACHINE_CONFIG_SCHEMA_VERSION,
        )
    return MachineConfig(
        path=candidate,
        status="model_ready",
        schema_version=MACHINE_CONFIG_SCHEMA_VERSION,
        embedding=MachineModelConfig(embedding_path, EMBEDDING_MODEL_NAME),
        reranker=MachineModelConfig(reranker_path, RERANKER_MODEL_NAME),
    )


def load_runtime_machine_config() -> MachineConfig:
    return load_machine_config(os.environ.get(MACHINE_CONFIG_ENV))


def require_runtime_machine_config() -> MachineConfig:
    return load_runtime_machine_config().require_ready()


def record_model_load_failed(role: str) -> None:
    if role in {"embedding", "reranker"}:
        _MODEL_LOAD_FAILURES.add(role)


def record_model_ready(role: str) -> None:
    _MODEL_LOAD_FAILURES.discard(role)


def write_machine_config(
    path: str | Path,
    *,
    embedding_model_path: str | Path,
    reranker_model_path: str | Path,
) -> MachineConfig:
    destination = Path(path).expanduser()
    if not destination.is_absolute():
        raise MachineConfigUnavailable("model_path_not_absolute")
    destination = destination.resolve(strict=False)
    if destination.exists():
        existing = load_machine_config(destination)
        if existing.status == "schema_unsupported":
            raise MachineConfigUnavailable("schema_unsupported")
    payload = {
        "schema_version": MACHINE_CONFIG_SCHEMA_VERSION,
        "high_quality_search": {
            "embedding_model_path": str(Path(embedding_model_path).expanduser().resolve(strict=False)),
            "reranker_model_path": str(Path(reranker_model_path).expanduser().resolve(strict=False)),
        },
    }
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    backup = destination.with_name(f"{destination.name}.bak")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    validation = load_machine_config(temporary)
    if not validation.ready:
        temporary.unlink(missing_ok=True)
        raise MachineConfigUnavailable(validation.error_code or validation.status)
    try:
        if destination.is_file():
            backup_temporary = backup.with_name(f".{backup.name}.{uuid4().hex}.tmp")
            try:
                backup_temporary.write_bytes(destination.read_bytes())
                os.replace(backup_temporary, backup)
            finally:
                backup_temporary.unlink(missing_ok=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return load_machine_config(destination).require_ready()


def _valid_embedding_structure(path: Path) -> bool:
    if not _required_files(path, "config.json", "modules.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors"):
        return False
    config = _read_json(path / "config.json")
    modules = _read_json(path / "modules.json")
    if not isinstance(config, dict) or config.get("model_type") != "qwen3" or config.get("hidden_size") != 1024:
        return False
    if not isinstance(modules, list):
        return False
    module_types = {str(item.get("type") or "") for item in modules if isinstance(item, dict)}
    return any(value.endswith(".Pooling") for value in module_types) and any(value.endswith(".Normalize") for value in module_types)


def _valid_reranker_structure(path: Path) -> bool:
    if not _required_files(path, "config.json", "modules.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors", "config_sentence_transformers.json"):
        return False
    config = _read_json(path / "config.json")
    sentence_config = _read_json(path / "config_sentence_transformers.json")
    modules = _read_json(path / "modules.json")
    if not isinstance(config, dict) or config.get("model_type") != "qwen3" or config.get("hidden_size") != 1024:
        return False
    if not isinstance(sentence_config, dict) or sentence_config.get("model_type") != "CrossEncoder":
        return False
    if not isinstance(modules, list):
        return False
    module_types = {str(item.get("type") or "") for item in modules if isinstance(item, dict)}
    return any(value.endswith(".LogitScore") for value in module_types)


def _required_files(path: Path, *names: str) -> bool:
    return all((path / name).is_file() and (path / name).stat().st_size > 0 for name in names)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _public_model_status(
    model: MachineModelConfig | None,
    *,
    load_failed: bool = False,
) -> dict[str, Any]:
    if model is None:
        return {"configured": False, "ready": False}
    return {
        "configured": True,
        "ready": not load_failed,
        "state": "model_load_failed" if load_failed else "model_ready",
        "name": model.name,
        "directory_name": model.basename,
        "path_hash": model.path_hash,
    }


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\x00" not in value
