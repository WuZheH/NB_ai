from __future__ import annotations

from dataclasses import dataclass
import json
import socket
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HealthResult:
    ready: bool
    error_code: str | None = None
    duration_seconds: float = 0.0


def check_json_health(
    url: str,
    *,
    validator: Callable[[dict[str, Any]], bool],
    timeout_seconds: float = 2.0,
) -> HealthResult:
    started = time.monotonic()
    try:
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - loopback URL from config
            if response.status != 200:
                return HealthResult(False, f"http_{response.status}", time.monotonic() - started)
            value = json.loads(response.read(1_048_576))
            if not isinstance(value, dict) or not validator(value):
                return HealthResult(False, "unexpected_health_payload", time.monotonic() - started)
            return HealthResult(True, duration_seconds=time.monotonic() - started)
    except HTTPError as exc:
        return HealthResult(False, f"http_{exc.code}", time.monotonic() - started)
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return HealthResult(False, "health_unreachable", time.monotonic() - started)


def check_fastapi_health(url: str) -> HealthResult:
    """Run a cheap liveness probe that is safe during model/LanceDB work.

    ``/api/v1/retrieval/index/status`` is a diagnostic endpoint that opens and
    fingerprints derived assets.  Polling it every supervisor tick can race a
    live LanceDB query on Windows, so it must not be used for liveness.
    """

    return check_json_health(
        f"{url.rstrip('/')}/health",
        validator=lambda value: value.get("status") == "ok"
        and value.get("app") == "NOTEBOOK_AI",
    )


def check_mcp_health(port: int) -> HealthResult:
    return check_json_health(
        f"http://127.0.0.1:{port}/healthz",
        validator=lambda value: value.get("status") == "ok"
        and value.get("service") == "notebook-ai-mcp",
    )


def check_http_ready(url: str, *, timeout_seconds: float = 2.0) -> HealthResult:
    """Verify an explicitly configured local readiness endpoint."""

    started = time.monotonic()
    try:
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - validated loopback URL
            response.read(4096)
            if 200 <= response.status < 300:
                return HealthResult(True, duration_seconds=time.monotonic() - started)
            return HealthResult(
                False,
                f"http_{response.status}",
                time.monotonic() - started,
            )
    except HTTPError as exc:
        return HealthResult(False, f"http_{exc.code}", time.monotonic() - started)
    except (URLError, TimeoutError, OSError):
        return HealthResult(False, "health_unreachable", time.monotonic() - started)


def port_is_listening(port: int, *, timeout_seconds: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def wait_for_health(
    check: Callable[[], HealthResult],
    *,
    timeout_seconds: float,
    process_alive: Callable[[], bool] | None = None,
    poll_seconds: float = 0.25,
) -> HealthResult:
    deadline = time.monotonic() + timeout_seconds
    last = HealthResult(False, "health_timeout")
    while time.monotonic() < deadline:
        if process_alive is not None and not process_alive():
            return HealthResult(False, "process_exited")
        last = check()
        if last.ready:
            return last
        time.sleep(min(poll_seconds, max(0.01, deadline - time.monotonic())))
    return HealthResult(False, last.error_code or "health_timeout")
