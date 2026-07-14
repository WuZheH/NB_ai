from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Any


ALLOWED_LOG_FIELDS = frozenset(
    {
        "timestamp",
        "component",
        "state",
        "pid",
        "port",
        "duration",
        "error_code",
        "restart_count",
    }
)


class RuntimeMetadataLogger:
    """Append metadata-only runtime events; corpus fields are impossible here."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()

    def log(
        self,
        *,
        component: str,
        state: str,
        pid: int | None = None,
        port: int | None = None,
        duration: float | None = None,
        error_code: str | None = None,
        restart_count: int = 0,
    ) -> None:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": _bounded_token(component),
            "state": _bounded_token(state),
            "restart_count": max(0, int(restart_count)),
        }
        if pid is not None:
            payload["pid"] = int(pid)
        if port is not None:
            payload["port"] = int(port)
        if duration is not None:
            payload["duration"] = round(max(0.0, float(duration)), 3)
        if error_code:
            payload["error_code"] = _bounded_token(error_code)
        if not set(payload).issubset(ALLOWED_LOG_FIELDS):
            raise ValueError("runtime log payload contains forbidden fields")
        line = json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
                stream.flush()
        except OSError:
            # Runtime availability and child ownership must never depend on a
            # best-effort metadata log sink.
            return


def _bounded_token(value: str) -> str:
    token = str(value).strip()
    if not token or len(token) > 96:
        raise ValueError("runtime metadata token is invalid")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
    if any(character not in allowed for character in token):
        raise ValueError("runtime metadata token contains unsafe characters")
    return token
