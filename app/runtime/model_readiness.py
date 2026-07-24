from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any


MODEL_ROLES = ("embedding", "reranker")
MODEL_STATES = {"unconfigured", "loading", "ready", "failed", "recovering"}
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")


@dataclass
class _ModelState:
    state: str = "unconfigured"
    error_code: str | None = None
    last_state_change: str | None = None


class ModelReadinessRegistry:
    """Process-local authority for executable retrieval-model readiness."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._api_ready = False
        self._configured = False
        self._states = {role: _ModelState() for role in MODEL_ROLES}
        self._last_state_change = _timestamp()

    def configure(self, *, configured: bool, error_code: str | None = None) -> None:
        with self._lock:
            if configured and self._configured:
                return
            self._configured = bool(configured)
            safe_error = _safe_error_code(error_code)
            for role in MODEL_ROLES:
                self._transition(role, "unconfigured", safe_error)

    def set_api_ready(self, ready: bool) -> None:
        with self._lock:
            if self._api_ready == bool(ready):
                return
            self._api_ready = bool(ready)
            self._last_state_change = _timestamp()
            _write_transition_log(
                {
                    "component": "api",
                    "state": "ready" if ready else "stopped",
                    "error_code": None,
                    "timestamp": self._last_state_change,
                }
            )

    def mark_loading(self, role: str) -> None:
        with self._lock:
            current = self._require_role(role)
            state = "recovering" if current.state == "failed" else "loading"
            self._transition(role, state, None)

    def mark_ready(self, role: str) -> None:
        with self._lock:
            self._require_role(role)
            self._transition(role, "ready", None)

    def mark_failed(self, role: str, error_code: str) -> None:
        with self._lock:
            self._require_role(role)
            self._transition(role, "failed", _safe_error_code(error_code) or "model_load_failed")

    def state(self, role: str) -> str:
        with self._lock:
            return self._require_role(role).state

    def error_code(self, role: str) -> str | None:
        with self._lock:
            return self._require_role(role).error_code

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            embedding = self._states["embedding"]
            reranker = self._states["reranker"]
            states = (embedding.state, reranker.state)
            if "failed" in states:
                model_state = "failed"
            elif "recovering" in states:
                model_state = "recovering"
            elif "loading" in states:
                model_state = "loading"
            elif states == ("ready", "ready"):
                model_state = "ready"
            else:
                model_state = "unconfigured"
            last_error = reranker.error_code or embedding.error_code
            retrieval_ready = bool(
                self._api_ready
                and self._configured
                and embedding.state == "ready"
                and reranker.state == "ready"
            )
            return {
                "api_ready": self._api_ready,
                "retrieval_ready": retrieval_ready,
                "model_state": model_state,
                "embedding_state": embedding.state,
                "reranker_state": reranker.state,
                "last_model_error_code": last_error,
                "last_state_change": self._last_state_change,
            }

    def reset_for_tests(self) -> None:
        with self._lock:
            self._api_ready = False
            self._configured = False
            self._states = {role: _ModelState() for role in MODEL_ROLES}
            self._last_state_change = _timestamp()

    def _transition(self, role: str, state: str, error_code: str | None) -> None:
        if state not in MODEL_STATES:
            raise ValueError("invalid_model_state")
        current = self._states[role]
        if current.state == state and current.error_code == error_code:
            return
        changed_at = _timestamp()
        current.state = state
        current.error_code = error_code
        current.last_state_change = changed_at
        self._last_state_change = changed_at
        _write_transition_log(
            {
                "component": role,
                "state": state,
                "error_code": error_code,
                "timestamp": changed_at,
            }
        )

    def _require_role(self, role: str) -> _ModelState:
        if role not in self._states:
            raise ValueError("invalid_model_role")
        return self._states[role]


def configure_model_readiness(*, configured: bool, error_code: str | None = None) -> None:
    _REGISTRY.configure(configured=configured, error_code=error_code)


def set_api_ready(ready: bool) -> None:
    _REGISTRY.set_api_ready(ready)


def mark_model_loading(role: str) -> None:
    _REGISTRY.mark_loading(role)


def mark_model_ready(role: str) -> None:
    _REGISTRY.mark_ready(role)


def mark_model_failed(role: str, error_code: str) -> None:
    _REGISTRY.mark_failed(role, error_code)


def model_state(role: str) -> str:
    return _REGISTRY.state(role)


def model_error_code(role: str) -> str | None:
    return _REGISTRY.error_code(role)


def public_model_readiness() -> dict[str, Any]:
    return _REGISTRY.public_status()


def reset_model_readiness_for_tests() -> None:
    _REGISTRY.reset_for_tests()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_error_code(value: str | None) -> str | None:
    if value and _SAFE_ERROR_CODE.fullmatch(value):
        return value
    return None


def _write_transition_log(event: dict[str, Any]) -> None:
    """Persist only stable state metadata; never paths, stack traces, or content."""

    try:
        configured_dir = os.environ.get("SEARCH_LOG_DIR")
        if configured_dir:
            log_dir = Path(configured_dir).expanduser().resolve(strict=False)
        else:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if not local_app_data:
                return
            log_dir = Path(local_app_data).expanduser().resolve(strict=False) / "Search" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        payload = {"schema": "search.model-readiness.v1", **event}
        with (log_dir / "model-readiness.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
    except (OSError, UnicodeError, ValueError):
        return


_REGISTRY = ModelReadinessRegistry()
