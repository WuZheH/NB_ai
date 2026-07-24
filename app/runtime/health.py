from __future__ import annotations

from dataclasses import dataclass
import json
import socket
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


EXPECTED_MCP_TOOLS = frozenset({"search", "fetch", "export_evidence"})
MCP_WIDGET_MIME = "text/html;profile=mcp-app"
MAX_MCP_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class HealthResult:
    ready: bool
    error_code: str | None = None
    duration_seconds: float = 0.0
    details: dict[str, Any] | None = None


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
                return HealthResult(
                    False,
                    "unexpected_health_payload",
                    time.monotonic() - started,
                    value if isinstance(value, dict) else None,
                )
            return HealthResult(
                True,
                duration_seconds=time.monotonic() - started,
                details=value,
            )
    except HTTPError as exc:
        return HealthResult(False, f"http_{exc.code}", time.monotonic() - started)
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return HealthResult(False, "health_unreachable", time.monotonic() - started)


def check_fastapi_liveness(url: str) -> HealthResult:
    """Use the cheap endpoint only for an already-owned process monitor."""

    return check_json_health(
        f"{url.rstrip('/')}/health",
        validator=lambda value: value.get("status") == "ok"
        and value.get("app") == "Search",
    )


def check_fastapi_health(url: str, *, timeout_seconds: float = 5.0) -> HealthResult:
    """Verify executable model readiness plus a valid read-only index state."""

    write_flags = (
        "db_write_performed",
        "production_db_write_performed",
        "zotero_db_write_performed",
        "vector_write_performed",
    )
    readiness = check_json_health(
        f"{url.rstrip('/')}/health",
        validator=lambda value: value.get("status") == "ok"
        and value.get("app") == "Search"
        and value.get("api_ready") is True
        and value.get("retrieval_ready") is True
        and value.get("model_state") == "ready"
        and value.get("embedding_state") == "ready"
        and value.get("reranker_state") == "ready"
        and all(value.get(flag) in {None, False} for flag in write_flags),
        timeout_seconds=timeout_seconds,
    )
    if not readiness.ready:
        return HealthResult(
            False,
            "backend_retrieval_not_ready"
            if readiness.error_code == "unexpected_health_payload"
            else readiness.error_code,
            readiness.duration_seconds,
            readiness.details,
        )
    result = check_json_health(
        f"{url.rstrip('/')}/api/v1/retrieval/index/status",
        validator=lambda value: (
            value.get("status") == "ready" and value.get("ready") is True
            or (
                value.get("status") == "missing"
                and value.get("ready") is False
                and value.get("data_state") == "empty_library"
                and isinstance(value.get("library_database_exists"), bool)
                and value.get("library_has_documents") is False
                and value.get("index_exists") is False
                and value.get("manifest_exists") is False
                and value.get("reasons") == ["index_and_manifest_missing"]
            )
        )
        and all(value.get(flag) in {None, False} for flag in write_flags),
        timeout_seconds=timeout_seconds,
    )
    if result.error_code == "unexpected_health_payload":
        return HealthResult(
            False,
            "backend_index_not_ready",
            result.duration_seconds,
            readiness.details,
        )
    return HealthResult(
        result.ready,
        result.error_code,
        result.duration_seconds,
        readiness.details,
    )


def check_mcp_health(port: int) -> HealthResult:
    return check_json_health(
        f"http://127.0.0.1:{port}/healthz",
        validator=lambda value: value.get("status") == "ok"
        and value.get("service") == "notebook-ai-mcp",
    )


def check_mcp_contract(port: int, *, timeout_seconds: float = 2.0) -> HealthResult:
    """Verify health, the three read-only tools, and the MCP Apps widget MIME."""

    started = time.monotonic()
    health = check_mcp_health(port)
    if not health.ready:
        return health
    try:
        tools = _mcp_request(port, "tools/list", {}, timeout_seconds=timeout_seconds)
        listed = tools.get("tools")
        if not isinstance(listed, list):
            return HealthResult(False, "mcp_tool_metadata_invalid", time.monotonic() - started)
        by_name = {
            str(tool.get("name")): tool
            for tool in listed
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        }
        if set(by_name) != EXPECTED_MCP_TOOLS:
            return HealthResult(False, "mcp_tool_metadata_invalid", time.monotonic() - started)
        if any(
            not isinstance(tool.get("inputSchema"), dict)
            or not isinstance(tool.get("outputSchema"), dict)
            or tool.get("_meta", {}).get("notebookAi/errorContract") != "isError-content-v1"
            or tool.get("annotations", {}).get("readOnlyHint") is not True
            for tool in by_name.values()
        ):
            return HealthResult(False, "mcp_tool_metadata_invalid", time.monotonic() - started)

        resources = _mcp_request(port, "resources/list", {}, timeout_seconds=timeout_seconds)
        listed_resources = resources.get("resources")
        if not isinstance(listed_resources, list):
            return HealthResult(False, "mcp_widget_resource_missing", time.monotonic() - started)
        widget = next(
            (
                resource
                for resource in listed_resources
                if isinstance(resource, dict)
                and str(resource.get("uri", "")).startswith("ui://")
            ),
            None,
        )
        if widget is None:
            return HealthResult(False, "mcp_widget_resource_missing", time.monotonic() - started)
        if widget.get("mimeType") != MCP_WIDGET_MIME:
            return HealthResult(False, "mcp_widget_mime_invalid", time.monotonic() - started)

        resource = _mcp_request(
            port,
            "resources/read",
            {"uri": widget["uri"]},
            timeout_seconds=timeout_seconds,
        )
        contents = resource.get("contents")
        item = contents[0] if isinstance(contents, list) and contents else None
        if not isinstance(item, dict) or item.get("mimeType") != MCP_WIDGET_MIME:
            return HealthResult(False, "mcp_widget_mime_invalid", time.monotonic() - started)
        if not isinstance(item.get("text"), str) or not item["text"].strip():
            return HealthResult(False, "mcp_widget_content_missing", time.monotonic() - started)
        return HealthResult(True, duration_seconds=time.monotonic() - started)
    except HTTPError as exc:
        return HealthResult(False, f"mcp_contract_http_{exc.code}", time.monotonic() - started)
    except (URLError, TimeoutError, OSError):
        return HealthResult(False, "mcp_contract_unreachable", time.monotonic() - started)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return HealthResult(False, "mcp_contract_invalid", time.monotonic() - started)


def _mcp_request(
    port: int,
    method: str,
    params: dict[str, Any],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        f"http://127.0.0.1:{port}/mcp",
        data=payload,
        method="POST",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed loopback URL
        if response.status != 200:
            raise ValueError(f"unexpected MCP status: {response.status}")
        raw = response.read(MAX_MCP_RESPONSE_BYTES + 1)
    if len(raw) > MAX_MCP_RESPONSE_BYTES:
        raise ValueError("MCP response too large")
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
        raise ValueError("invalid MCP response")
    return value["result"]


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
