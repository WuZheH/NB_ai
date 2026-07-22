from __future__ import annotations

from dataclasses import replace
import json
from io import BytesIO
import os
from pathlib import Path
import subprocess

import pytest

from app.runtime import health as runtime_health
from app.runtime.cli import build_parser
from app.runtime.config import RuntimeConfig
from app.runtime.contracts import (
    ComponentName,
    ComponentState,
    ComponentStatus,
    ControlRequest,
    ProcessIdentity,
    RuntimeState,
    RuntimeStatus,
    TunnelState,
)
from app.runtime.health import HealthResult
from app.runtime.logging import ALLOWED_LOG_FIELDS, RuntimeMetadataLogger
from app.runtime.pid_identity import identity_matches
from app.runtime.pid_identity import ProcessIdentityUnavailable
from app.runtime.process_manager import (
    ManagedProcess,
    ProcessManager,
    ProcessSpec,
    ProcessStartError,
)
from app.runtime.supervisor import (
    ControlRequestQueue,
    RuntimeController,
    RuntimeStartupError,
    RuntimeSupervisor,
    SingleInstanceLock,
)
from app.runtime.tunnel import ChatGptTunnelStatus


def test_fastapi_startup_uses_index_readiness_and_monitor_uses_search_liveness(
    monkeypatch,
) -> None:
    observed: list[tuple[str, bool, bool, bool]] = []

    def fake_check(url: str, *, validator, timeout_seconds: float = 2.0):
        observed.append((
            url,
            validator({"status": "ok", "app": "Search"}),
            validator({"status": "ok", "app": "NOTEBOOK_AI"}),
            validator({"status": "ready", "ready": True}),
        ))
        return HealthResult(True)

    monkeypatch.setattr(runtime_health, "check_json_health", fake_check)
    assert runtime_health.check_fastapi_health("http://127.0.0.1:8000").ready
    assert runtime_health.check_fastapi_liveness("http://127.0.0.1:8000").ready
    assert observed == [
        (
            "http://127.0.0.1:8000/api/v1/retrieval/index/status",
            False,
            False,
            True,
        ),
        ("http://127.0.0.1:8000/health", True, False, False),
    ]


def test_fastapi_readiness_rejects_write_flags(monkeypatch) -> None:
    def fake_check(url: str, *, validator, timeout_seconds: float = 2.0):
        del url, timeout_seconds
        assert not validator(
            {
                "status": "ready",
                "ready": True,
                "production_db_write_performed": True,
            }
        )
        return HealthResult(False, "unexpected_health_payload")

    monkeypatch.setattr(runtime_health, "check_json_health", fake_check)
    result = runtime_health.check_fastapi_health("http://127.0.0.1:8000")
    assert result.ready is False
    assert result.error_code == "backend_index_not_ready"


def test_fastapi_readiness_accepts_only_a_safe_empty_library(monkeypatch) -> None:
    observed: list[bool] = []

    def fake_check(url: str, *, validator, timeout_seconds: float = 2.0):
        del url, timeout_seconds
        safe_empty = {
            "status": "missing",
            "ready": False,
            "data_state": "empty_library",
            "library_database_exists": False,
            "library_has_documents": False,
            "index_exists": False,
            "manifest_exists": False,
            "reasons": ["index_and_manifest_missing"],
            "production_db_write_performed": False,
        }
        observed.extend(
            (
                validator(safe_empty),
                validator({**safe_empty, "data_state": "configured"}),
                validator({**safe_empty, "library_database_exists": True}),
                validator({**safe_empty, "library_has_documents": True}),
                validator({**safe_empty, "library_has_documents": None}),
                validator({**safe_empty, "status": "corrupt"}),
                validator({**safe_empty, "production_db_write_performed": True}),
            )
        )
        return HealthResult(True)

    monkeypatch.setattr(runtime_health, "check_json_health", fake_check)
    assert runtime_health.check_fastapi_health("http://127.0.0.1:8000").ready
    assert observed == [True, False, True, False, False, False, False]


def test_mcp_contract_requires_three_read_only_tools_and_widget_mime(monkeypatch) -> None:
    monkeypatch.setattr(runtime_health, "check_mcp_health", lambda port: HealthResult(True))

    def request(port: int, method: str, params: dict[str, object], *, timeout_seconds: float):
        del port, params, timeout_seconds
        if method == "tools/list":
            return {
                "tools": [
                    {"name": name, "inputSchema": {}, "annotations": {"readOnlyHint": True}}
                    for name in ("search", "fetch", "export_evidence")
                ]
            }
        if method == "resources/list":
            return {
                "resources": [
                    {"uri": "ui://notebook-ai/search-widget", "mimeType": runtime_health.MCP_WIDGET_MIME}
                ]
            }
        return {
            "contents": [
                {"mimeType": runtime_health.MCP_WIDGET_MIME, "text": "<html>widget</html>"}
            ]
        }

    monkeypatch.setattr(runtime_health, "_mcp_request", request)
    assert runtime_health.check_mcp_contract(8787).ready is True


def _config(tmp_path: Path) -> RuntimeConfig:
    project = tmp_path / "project"
    project.mkdir()
    return RuntimeConfig.load(
        project_root=project,
        env={
            "LOCALAPPDATA": str(tmp_path / "local"),
            "NOTEBOOK_AI_PYTHON_EXE": str(tmp_path / "python.exe"),
            "NOTEBOOK_AI_NODE_EXE": str(tmp_path / "node.exe"),
        },
    )


def test_runtime_paths_are_scoped_to_current_user_local_app_data(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.paths.runtime_dir == (tmp_path / "local" / "Search" / "runtime").resolve()
    assert config.paths.logs_dir == (tmp_path / "local" / "Search" / "logs").resolve()
    assert config.paths.config_dir == (tmp_path / "local" / "Search" / "config").resolve()
    assert config.paths.legacy_runtime_config_file == (
        tmp_path / "local" / "NOTEBOOK_AI" / "config" / "runtime.json"
    ).resolve()
    assert config.python_exe.name == "python.exe"
    assert config.paths.runtime_root == (tmp_path / "project").resolve()
    assert config.paths.data_project_root == config.paths.runtime_root
    assert config.paths.mcp_server_entry.as_posix().endswith("dist/server/index.js")


def test_search_environment_names_take_priority_and_roaming_config_is_separate(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "portable-data"
    config = RuntimeConfig.load(
        project_root=project,
        env={
            "LOCALAPPDATA": str(tmp_path / "local"),
            "APPDATA": str(tmp_path / "roaming"),
            "SEARCH_DATA_DIR": str(data_dir),
            "SEARCH_PYTHON": str(tmp_path / "search-python.exe"),
            "SEARCH_NODE": str(tmp_path / "search-node.exe"),
            "SEARCH_BACKEND_PORT": "18001",
            "SEARCH_MCP_PORT": "18788",
            "SEARCH_RUNTIME_DIR": str(tmp_path / "custom-runtime"),
            "SEARCH_LOG_DIR": str(tmp_path / "custom-logs"),
            "SEARCH_CONFIG_DIR": str(tmp_path / "custom-config"),
            "NOTEBOOK_AI_PYTHON_EXE": str(tmp_path / "legacy-python.exe"),
            "NOTEBOOK_AI_NODE_EXE": str(tmp_path / "legacy-node.exe"),
        },
    )
    assert config.paths.data_dir == data_dir.resolve()
    assert config.paths.data_project_root == data_dir.parent.resolve()
    assert config.paths.config_dir == (tmp_path / "custom-config").resolve()
    assert config.paths.logs_dir == (tmp_path / "custom-logs").resolve()
    assert config.paths.runtime_dir == (tmp_path / "custom-runtime").resolve()
    assert config.python_exe.name == "search-python.exe"
    assert config.node_exe.name == "search-node.exe"
    assert config.backend_port == 18001
    assert config.mcp_port == 18788


def test_runtime_config_reads_legacy_config_without_copying_it(tmp_path: Path) -> None:
    local_root = tmp_path / "local"
    legacy_config = local_root / "NOTEBOOK_AI" / "config" / "runtime.json"
    legacy_config.parent.mkdir(parents=True)
    legacy_config.write_text(
        json.dumps({
            "schema_version": "notebook_ai.runtime.config.v1",
            "backend_port": 18080,
            "mcp_port": 18787,
        }),
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    config = RuntimeConfig.load(
        project_root=project,
        env={
            "LOCALAPPDATA": str(local_root),
            "NOTEBOOK_AI_PYTHON_EXE": str(tmp_path / "python.exe"),
            "NOTEBOOK_AI_NODE_EXE": str(tmp_path / "node.exe"),
        },
    )
    assert config.backend_port == 18080
    assert config.mcp_port == 18787
    assert not config.paths.runtime_config_file.exists()
    assert legacy_config.is_file()


def test_runtime_paths_split_packaged_code_from_stable_data(tmp_path: Path) -> None:
    runtime_root = tmp_path / "package" / "runtime-project"
    data_root = tmp_path / "stable-project"
    runtime_root.mkdir(parents=True)
    data_root.mkdir()
    config = RuntimeConfig.load(
        runtime_root=runtime_root,
        data_project_root=data_root,
        env={
            "LOCALAPPDATA": str(tmp_path / "local"),
            "NOTEBOOK_AI_PYTHON_EXE": str(tmp_path / "python.exe"),
            "NOTEBOOK_AI_NODE_EXE": str(tmp_path / "node.exe"),
        },
    )
    assert config.paths.runtime_root == runtime_root.resolve()
    assert config.paths.data_project_root == data_root.resolve()
    assert config.paths.launcher_script.is_relative_to(runtime_root)
    assert config.paths.mcp_server_entry.is_relative_to(runtime_root)


def test_runtime_cli_exposes_local_lifecycle_and_read_only_tunnel_status() -> None:
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if action.dest == "command"  # noqa: SLF001
    )
    assert {
        "start",
        "ensure-running",
        "stop",
        "restart",
        "status",
        "doctor",
        "logs",
        "open-web",
        "tunnel-status",
        "signal",
    }.issubset(subparsers.choices)
    assert "configure-tunnel" not in subparsers.choices
    assert "tunnel-doctor" not in subparsers.choices
    signal_parser = subparsers.choices["signal"]
    action = next(
        item for item in signal_parser._actions if item.dest == "action"  # noqa: SLF001
    )
    assert set(action.choices) == {"restart", "sync_zotero_notes"}


def test_single_instance_lock_rejects_second_holder(tmp_path: Path) -> None:
    first = SingleInstanceLock(tmp_path / "runtime" / "supervisor.lock")
    second = SingleInstanceLock(tmp_path / "runtime" / "supervisor.lock")
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()


def test_control_queue_is_atomic_compact_and_rejects_private_fields(tmp_path: Path) -> None:
    queue = ControlRequestQueue(tmp_path / "control")
    request = ControlRequest(
        action="sync_zotero_notes",
        request_id="request-1",
        timestamp="2026-07-13T00:00:00+00:00",
    )
    path = queue.submit(request)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"schema_version", "action", "request_id", "timestamp"}
    assert queue.consume() == [request]
    assert not path.exists()

    invalid = queue.directory / "bad.json"
    invalid.write_text(
        json.dumps({**request.to_dict(), "fragment_id": "private"}),
        encoding="utf-8",
    )
    assert queue.consume() == []
    assert not invalid.exists()

    with pytest.raises(ValueError, match="request id"):
        ControlRequest(
            action="restart",
            request_id="../outside",
            timestamp="2026-07-13T00:00:00+00:00",
        )
    with pytest.raises(ValueError, match="timestamp"):
        ControlRequest(
            action="restart",
            request_id="safe-request",
            timestamp="private note text",
        )


def test_runtime_logger_emits_only_allowlisted_metadata(tmp_path: Path) -> None:
    path = tmp_path / "runtime.jsonl"
    logger = RuntimeMetadataLogger(path)
    logger.log(
        component="fastapi",
        state="ready",
        pid=123,
        port=8000,
        duration=1.23456,
        restart_count=1,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload).issubset(ALLOWED_LOG_FIELDS)
    assert not set(payload).intersection(
        {"query", "fragment_id", "provenance", "note_text", "pdf_text", "secret"}
    )
    with pytest.raises(ValueError):
        logger.log(component="fastapi", state="contains private text")


def test_runtime_logger_io_failure_is_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logger = RuntimeMetadataLogger(tmp_path / "runtime.jsonl")
    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable")),
    )
    logger.log(component="fastapi", state="ready")


def test_pid_identity_checks_creation_time_and_executable() -> None:
    expected = ProcessIdentity(42, 100.0, r"D:\Python\python.exe")
    assert identity_matches(expected, ProcessIdentity(42, 100.0, r"D:\Python\python.exe"))
    assert not identity_matches(expected, ProcessIdentity(42, 101.0, r"D:\Python\python.exe"))
    assert not identity_matches(expected, ProcessIdentity(42, 100.0, r"D:\Node\node.exe"))


def test_windows_termination_verifies_identity_on_the_termination_handle() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "app" / "runtime" / "pid_identity.py"
    ).read_text(encoding="utf-8")
    assert "_get_windows_identity_from_handle(handle, identity.pid)" in source
    assert "identity_matches(identity, observed)" in source


def test_process_manager_uses_hidden_console_and_no_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "tool.exe"
    executable.write_bytes(b"")
    captured: dict[str, object] = {}

    class FakePopen:
        pid = 99

        def __init__(self, *args: object, **kwargs: object):
            captured["args"] = args
            captured.update(kwargs)

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        "app.runtime.process_manager.get_process_identity",
        lambda pid: ProcessIdentity(pid, 1.0, str(executable)),
    )
    spec = ProcessSpec("test", executable, ("run",), tmp_path)
    ProcessManager().spawn(spec)
    assert captured["shell"] is False
    assert captured["stdout"] is subprocess.DEVNULL
    if os.name == "nt":
        flags = int(captured["creationflags"])
        assert flags & subprocess.CREATE_NO_WINDOW
        assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
        assert not flags & subprocess.DETACHED_PROCESS
        startup_info = captured["startupinfo"]
        assert startup_info.dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert startup_info.wShowWindow == subprocess.SW_HIDE


def test_process_manager_reaps_child_when_identity_capture_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "tool.exe"
    executable.write_bytes(b"")
    calls: list[str] = []

    class FakePopen:
        pid = 100

        def __init__(self, *args: object, **kwargs: object):
            pass

        def terminate(self) -> None:
            calls.append("terminate")

        def wait(self, timeout: float | None = None) -> int:
            calls.append("wait")
            return 0

        def poll(self) -> int | None:
            return None

        def kill(self) -> None:
            calls.append("kill")

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        "app.runtime.process_manager.get_process_identity",
        lambda pid: (_ for _ in ()).throw(ProcessIdentityUnavailable("unavailable")),
    )
    with pytest.raises(ProcessStartError, match="identify"):
        ProcessManager().spawn(ProcessSpec("test", executable, (), tmp_path))
    assert calls == ["terminate", "wait"]


def test_controller_start_is_idempotent_when_supervisor_identity_is_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    identity = ProcessIdentity(55, 10.0, str(config.python_exe))
    status = RuntimeStatus(
        state=RuntimeState.LOCAL_READY_TUNNEL_MISSING,
        updated_at="now",
        components={
            "supervisor": ComponentStatus(
                component=ComponentName.SUPERVISOR,
                state=ComponentState.READY,
                pid=55,
                owned=True,
                identity=identity,
            )
        },
    )
    controller = RuntimeController(config)
    monkeypatch.setattr(controller, "status", lambda: status)
    monkeypatch.setattr("app.runtime.supervisor.process_is_alive", lambda value: True)
    monkeypatch.setattr(
        controller.process_manager,
        "spawn",
        lambda *args, **kwargs: pytest.fail("idempotent start spawned a duplicate"),
    )
    assert controller.start() is status


def test_status_keeps_live_supervisor_in_starting_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    identity = ProcessIdentity(56, 10.0, str(config.python_exe))
    status = RuntimeStatus(
        state=RuntimeState.STARTING,
        updated_at="now",
        components={
            "supervisor": ComponentStatus(
                component=ComponentName.SUPERVISOR,
                state=ComponentState.STARTING,
                pid=56,
                owned=True,
                identity=identity,
            )
        },
    )
    monkeypatch.setattr("app.runtime.supervisor.process_is_alive", lambda value: True)
    assert RuntimeController(config)._reconcile_status(status).state is RuntimeState.STARTING


def test_controller_returns_live_starting_status_during_bounded_health_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    identity = ProcessIdentity(57, 10.0, str(config.python_exe))
    stopped = RuntimeStatus(state=RuntimeState.STOPPED, updated_at="now")
    starting = RuntimeStatus(
        state=RuntimeState.STARTING,
        updated_at="later",
        components={
            "supervisor": ComponentStatus(
                component=ComponentName.SUPERVISOR,
                state=ComponentState.STARTING,
                owned=True,
                identity=identity,
            )
        },
    )
    controller = RuntimeController(config)
    monkeypatch.setattr(controller, "status", lambda: stopped)
    monkeypatch.setattr(controller, "_read_persisted_status", lambda: starting)
    monkeypatch.setattr(controller.process_manager, "spawn", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.runtime.supervisor.process_is_alive", lambda value: True)
    assert controller.start(wait_seconds=0.01) is starting


def test_remote_mode_refuses_to_start_local_processes(tmp_path: Path) -> None:
    config = replace(_config(tmp_path), mode="remote")
    with pytest.raises(RuntimeStartupError, match="remote_mode"):
        RuntimeController(config).start(wait_seconds=0)


def test_missing_mcp_build_is_reported_after_fastapi_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = RuntimeSupervisor(_config(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(supervisor, "_start_fastapi", lambda: calls.append("fastapi"))
    monkeypatch.setattr(supervisor, "_rollback", lambda: calls.append("rollback"))
    monkeypatch.setattr(
        supervisor,
        "_refresh_tunnel_status",
        lambda: pytest.fail("tunnel was probed before MCP build validation"),
    )
    with pytest.raises(RuntimeStartupError, match="mcp_build_missing"):
        supervisor.start_components()
    assert calls == ["fastapi", "rollback"]
    assert supervisor._managed == {}


def test_startup_never_runs_note_index_status_or_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config.paths.mcp_server_entry.parent.mkdir(parents=True)
    config.paths.mcp_server_entry.write_text("built", encoding="utf-8")
    supervisor = RuntimeSupervisor(config)
    assert not hasattr(supervisor, "tunnel")
    monkeypatch.setattr(
        supervisor,
        "_ensure_note_index",
        lambda **kwargs: pytest.fail("startup attempted a note-index operation"),
    )
    monkeypatch.setattr(
        supervisor,
        "_start_fastapi",
        lambda: supervisor._set_external(ComponentName.FASTAPI, config.backend_port),
    )
    monkeypatch.setattr(
        supervisor,
        "_start_mcp",
        lambda: supervisor._set_external(ComponentName.MCP, config.mcp_port),
    )
    monkeypatch.setattr(
        supervisor.tunnel_probe,
        "diagnose",
        lambda: ChatGptTunnelStatus("none", TunnelState.NOT_CONFIGURED),
    )
    monkeypatch.setattr(
        supervisor,
        "_persist",
        lambda state, **kwargs: setattr(supervisor.status, "state", state),
    )
    assert supervisor.start_components().state is RuntimeState.LOCAL_READY_TUNNEL_MISSING


def test_note_index_sync_rejects_missing_read_only_safety_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = RuntimeSupervisor(_config(tmp_path))
    responses = iter([{"status": "ready"}, {"status": "ready"}])
    monkeypatch.setattr(
        supervisor,
        "_run_note_index_command",
        lambda *args, **kwargs: next(responses),
    )
    with pytest.raises(RuntimeStartupError, match="zotero_note_index_sync_failed"):
        supervisor._ensure_note_index(sync_if_missing=True, incremental_sync=True)


def test_note_index_subprocess_identity_is_persisted_and_then_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = RuntimeSupervisor(_config(tmp_path))
    observations: list[bool] = []

    class FakePopen:
        pid = 88
        returncode = 0

        def __init__(self, *args: object, **kwargs: object):
            self.stdout = BytesIO(b'{"status":"ready"}')

        def poll(self) -> int:
            return 0

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            return (b'{"status":"ready"}', b"")

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        "app.runtime.supervisor.get_process_identity",
        lambda pid: ProcessIdentity(pid, 1.0, str(supervisor.config.python_exe)),
    )

    def persist(*args: object, **kwargs: object) -> None:
        current = supervisor.status.components.get("zotero_note_index")
        observations.append(bool(current and current.owned and current.identity))

    monkeypatch.setattr(supervisor, "_persist", persist)
    result = supervisor._run_note_index_command(
        supervisor.config.paths.note_status_script,
        timeout_seconds=1,
    )
    assert result == {"status": "ready"}
    assert observations[0] is True
    assert observations[-1] is False
    assert ComponentName.ZOTERO_NOTE_INDEX not in supervisor._managed


def test_fastapi_port_conflict_never_starts_or_kills_unknown_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = RuntimeSupervisor(_config(tmp_path))
    monkeypatch.setattr(
        "app.runtime.supervisor.check_fastapi_health",
        lambda url: HealthResult(False, "health_unreachable"),
    )
    monkeypatch.setattr("app.runtime.supervisor.port_is_listening", lambda port: True)
    monkeypatch.setattr(
        "app.runtime.supervisor.wait_for_health",
        lambda *args, **kwargs: HealthResult(False, "health_unreachable"),
    )
    monkeypatch.setattr(
        supervisor.process_manager,
        "spawn",
        lambda *args, **kwargs: pytest.fail("port conflict spawned a process"),
    )
    with pytest.raises(RuntimeStartupError, match="backend_health_unreachable"):
        supervisor._start_fastapi()


def test_mcp_port_conflict_reports_contract_error_without_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config.paths.mcp_server_entry.parent.mkdir(parents=True)
    config.paths.mcp_server_entry.write_text("built", encoding="utf-8")
    supervisor = RuntimeSupervisor(config)
    monkeypatch.setattr(
        "app.runtime.supervisor.check_mcp_contract",
        lambda port: HealthResult(False, "mcp_widget_mime_invalid"),
    )
    monkeypatch.setattr("app.runtime.supervisor.port_is_listening", lambda port: True)
    monkeypatch.setattr(
        "app.runtime.supervisor.wait_for_health",
        lambda *args, **kwargs: HealthResult(False, "mcp_widget_mime_invalid"),
    )
    monkeypatch.setattr(
        supervisor.process_manager,
        "spawn",
        lambda *args, **kwargs: pytest.fail("port conflict spawned a process"),
    )
    with pytest.raises(RuntimeStartupError, match="mcp_widget_mime_invalid"):
        supervisor._start_mcp()


def test_partial_start_failure_rolls_back_only_owned_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config.paths.mcp_server_entry.parent.mkdir(parents=True)
    config.paths.mcp_server_entry.write_text("built", encoding="utf-8")
    supervisor = RuntimeSupervisor(config)
    calls: list[str] = []
    monkeypatch.setattr(
        supervisor,
        "_ensure_note_index",
        lambda **kwargs: pytest.fail("startup attempted note-index work"),
    )
    monkeypatch.setattr(supervisor, "_start_fastapi", lambda: calls.append("fastapi"))
    monkeypatch.setattr(
        supervisor,
        "_start_mcp",
        lambda: (_ for _ in ()).throw(RuntimeStartupError("mcp_start_failed")),
    )
    monkeypatch.setattr(supervisor, "_rollback", lambda: calls.append("rollback"))
    with pytest.raises(RuntimeStartupError, match="mcp_start_failed"):
        supervisor.start_components()
    assert calls == ["fastapi", "rollback"]


def test_startup_detects_quick_tunnel_without_starting_or_stopping_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config.paths.mcp_server_entry.parent.mkdir(parents=True)
    config.paths.mcp_server_entry.write_text("built", encoding="utf-8")
    supervisor = RuntimeSupervisor(config)
    monkeypatch.setattr(
        supervisor,
        "_start_fastapi",
        lambda: supervisor._set_external(ComponentName.FASTAPI, config.backend_port),
    )
    monkeypatch.setattr(
        supervisor,
        "_start_mcp",
        lambda: supervisor._set_external(ComponentName.MCP, config.mcp_port),
    )
    monkeypatch.setattr(
        supervisor.tunnel_probe,
        "diagnose",
        lambda: ChatGptTunnelStatus(
            "quick",
            TunnelState.QUICK_ONLINE,
            pid=43120,
            public_url="https://temporary.trycloudflare.com",
        ),
    )
    monkeypatch.setattr(
        supervisor,
        "_rollback",
        lambda: pytest.fail("detection-only tunnel status rolled back local services"),
    )
    monkeypatch.setattr(
        supervisor,
        "_persist",
        lambda state, **kwargs: setattr(supervisor.status, "state", state),
    )
    status = supervisor.start_components()
    assert status.components["fastapi"].state is ComponentState.EXTERNAL
    assert status.components["mcp"].state is ComponentState.EXTERNAL
    assert status.components["tunnel"].state is ComponentState.EXTERNAL
    assert status.components["tunnel"].pid == 43120
    assert status.tunnel_state is TunnelState.QUICK_ONLINE
    assert status.state is RuntimeState.LOCAL_READY_TUNNEL_MISSING


def test_stop_never_claims_or_terminates_reused_external_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = RuntimeSupervisor(_config(tmp_path))
    supervisor.status.components[ComponentName.FASTAPI.value] = ComponentStatus(
        component=ComponentName.FASTAPI,
        state=ComponentState.EXTERNAL,
        port=8000,
        owned=False,
    )


def test_previous_owned_healthy_child_is_adopted_not_downgraded_to_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    identity = ProcessIdentity(71, 1.0, str(config.python_exe))
    previous = RuntimeStatus(
        state=RuntimeState.DEGRADED,
        updated_at="now",
        components={
            "fastapi": ComponentStatus(
                component=ComponentName.FASTAPI,
                state=ComponentState.READY,
                pid=71,
                port=config.backend_port,
                owned=True,
                identity=identity,
            )
        },
    )
    config.paths.runtime_dir.mkdir(parents=True)
    config.paths.status_file.write_text(
        json.dumps(previous.to_dict()), encoding="utf-8"
    )
    supervisor = RuntimeSupervisor(config)
    managed = ManagedProcess(supervisor._fastapi_spec(), identity)
    monkeypatch.setattr("app.runtime.supervisor.process_is_alive", lambda value: True)
    monkeypatch.setattr(
        supervisor.process_manager, "attach", lambda spec, value: managed
    )
    monkeypatch.setattr(supervisor.process_manager, "is_alive", lambda value: True)
    monkeypatch.setattr(
        "app.runtime.supervisor.check_fastapi_health", lambda url: HealthResult(True)
    )
    supervisor._adopt_previous_owned_components()
    supervisor._start_fastapi()
    adopted = supervisor.status.components["fastapi"]
    assert adopted.owned is True
    assert adopted.identity == identity
    assert supervisor._managed[ComponentName.FASTAPI] is managed


def test_external_service_health_loss_recovers_without_killing_external_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = RuntimeSupervisor(_config(tmp_path))
    supervisor.status.components["fastapi"] = ComponentStatus(
        component=ComponentName.FASTAPI,
        state=ComponentState.EXTERNAL,
        port=8000,
        owned=False,
    )
    monkeypatch.setattr(
        "app.runtime.supervisor.check_fastapi_liveness", lambda url: HealthResult(False)
    )
    monkeypatch.setattr(
        "app.runtime.supervisor.check_mcp_health", lambda port: HealthResult(True)
    )
    monkeypatch.setattr("app.runtime.supervisor.port_is_listening", lambda port: False)
    monkeypatch.setattr("app.runtime.supervisor.time.sleep", lambda value: None)
    started: list[str] = []

    def start_fastapi() -> None:
        started.append("fastapi")
        supervisor.status.components["fastapi"] = ComponentStatus(
            component=ComponentName.FASTAPI,
            state=ComponentState.READY,
            owned=True,
        )

    monkeypatch.setattr(supervisor, "_start_fastapi", start_fastapi)
    supervisor._monitor_external_components()
    assert started == ["fastapi"]
    assert supervisor.status.components["fastapi"].owned is True


def test_external_port_conflict_does_not_consume_restart_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = RuntimeSupervisor(_config(tmp_path))
    current = ComponentStatus(
        component=ComponentName.FASTAPI,
        state=ComponentState.EXTERNAL,
        port=8000,
        owned=False,
        restart_count=2,
    )
    supervisor.status.components["fastapi"] = current
    monkeypatch.setattr(
        "app.runtime.supervisor.check_fastapi_liveness", lambda url: HealthResult(False)
    )
    monkeypatch.setattr("app.runtime.supervisor.port_is_listening", lambda port: True)
    supervisor._monitor_external_components()
    assert current.restart_count == 2
    assert current.error_code == "backend_port_conflict"
    monkeypatch.setattr(
        supervisor.process_manager,
        "stop",
        lambda value: pytest.fail("external service was terminated"),
    )
    assert supervisor.stop_components() is True
    assert (
        supervisor.status.components[ComponentName.FASTAPI.value].state
        is ComponentState.EXTERNAL
    )


def test_failed_health_start_stops_child_and_removes_managed_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = RuntimeSupervisor(_config(tmp_path))
    spec = ProcessSpec("fastapi", Path("python.exe"), (), tmp_path, port=8000)
    process = ManagedProcess(spec, ProcessIdentity(61, 1.0, "python.exe"))
    stopped: list[ManagedProcess] = []
    monkeypatch.setattr(supervisor.process_manager, "spawn", lambda value: process)
    monkeypatch.setattr(supervisor.process_manager, "stop", stopped.append)
    monkeypatch.setattr(
        "app.runtime.supervisor.wait_for_health",
        lambda *args, **kwargs: HealthResult(False, "health_timeout"),
    )
    with pytest.raises(RuntimeStartupError, match="fastapi_health_timeout"):
        supervisor._spawn_and_wait(
            ComponentName.FASTAPI,
            spec,
            lambda: HealthResult(False),
        )
    assert stopped == [process]
    assert ComponentName.FASTAPI not in supervisor._managed
    assert (
        supervisor.status.components[ComponentName.FASTAPI.value].state
        is ComponentState.FAILED
    )


def test_dead_child_uses_bounded_backoff_and_restart_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = RuntimeSupervisor(replace(_config(tmp_path), max_restart_count=2))
    spec = ProcessSpec("fastapi", Path("python.exe"), (), tmp_path, port=8000)
    process = ManagedProcess(spec, ProcessIdentity(7, 1.0, "python.exe"))
    supervisor._managed[ComponentName.FASTAPI] = process
    supervisor.status.components[ComponentName.FASTAPI.value] = ComponentStatus(
        component=ComponentName.FASTAPI,
        state=ComponentState.READY,
        pid=7,
        owned=True,
        identity=process.identity,
    )
    sleeps: list[float] = []
    monkeypatch.setattr(supervisor.process_manager, "is_alive", lambda value: False)
    monkeypatch.setattr("app.runtime.supervisor.time.sleep", sleeps.append)

    def replace_fastapi() -> None:
        replacement = ComponentStatus(
            component=ComponentName.FASTAPI,
            state=ComponentState.READY,
            restart_count=0,
            owned=True,
        )
        supervisor.status.components[ComponentName.FASTAPI.value] = replacement

    monkeypatch.setattr(supervisor, "_start_fastapi", replace_fastapi)
    monkeypatch.setattr(supervisor, "_persist", lambda *args, **kwargs: None)
    supervisor._monitor_once()
    assert sleeps == [1.0]
    assert supervisor.status.components[ComponentName.FASTAPI.value].restart_count == 1


def test_runtime_note_sync_failure_degrades_only_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = RuntimeSupervisor(_config(tmp_path))
    local_process = ManagedProcess(
        supervisor._fastapi_spec(),
        ProcessIdentity(72, 1.0, str(supervisor.config.python_exe)),
    )
    supervisor._managed[ComponentName.FASTAPI] = local_process
    monkeypatch.setattr(
        supervisor,
        "_ensure_note_index",
        lambda **kwargs: (_ for _ in ()).throw(
            RuntimeStartupError("zotero_note_index_sync_failed")
        ),
    )
    monkeypatch.setattr(
        supervisor,
        "_rollback",
        lambda: pytest.fail("runtime note sync rolled back local services"),
    )
    monkeypatch.setattr(supervisor, "_safe_persist", lambda *args, **kwargs: None)
    monkeypatch.setattr(supervisor.logger, "log", lambda **kwargs: None)
    supervisor._sync_note_index_while_running()
    assert supervisor._managed[ComponentName.FASTAPI] is local_process
    assert supervisor.status.components["zotero_note_index"].state is ComponentState.DEGRADED


def test_supervisor_internal_persist_failure_rolls_back_owned_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = RuntimeSupervisor(_config(tmp_path))
    rollbacks: list[str] = []
    monkeypatch.setattr(supervisor, "_adopt_previous_owned_components", lambda: None)
    monkeypatch.setattr(supervisor, "_rollback", lambda: rollbacks.append("rollback"))
    monkeypatch.setattr(
        supervisor,
        "_persist",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("status unavailable")),
    )
    assert supervisor.supervise_forever() == 1
    assert rollbacks == ["rollback"]


def test_status_reconciles_stale_pid_and_health_before_claiming_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    supervisor_identity = ProcessIdentity(10, 1.0, "python.exe")
    fastapi_identity = ProcessIdentity(11, 1.0, "python.exe")
    mcp_identity = ProcessIdentity(12, 1.0, "node.exe")
    current = RuntimeStatus(
        state=RuntimeState.READY,
        updated_at="now",
        components={
            "supervisor": ComponentStatus(
                component=ComponentName.SUPERVISOR,
                state=ComponentState.READY,
                owned=True,
                identity=supervisor_identity,
            ),
            "fastapi": ComponentStatus(
                component=ComponentName.FASTAPI,
                state=ComponentState.READY,
                owned=True,
                identity=fastapi_identity,
            ),
            "mcp": ComponentStatus(
                component=ComponentName.MCP,
                state=ComponentState.READY,
                owned=True,
                identity=mcp_identity,
            ),
        },
    )
    monkeypatch.setattr(
        "app.runtime.supervisor.process_is_alive",
        lambda identity: identity.pid != fastapi_identity.pid,
    )
    monkeypatch.setattr(
        "app.runtime.supervisor.check_mcp_health",
        lambda port: HealthResult(True),
    )
    reconciled = RuntimeController(config)._reconcile_status(current)
    assert reconciled.state is RuntimeState.DEGRADED
    assert reconciled.components["fastapi"].error_code == "fastapi_identity_stale"


def test_previous_tunnel_status_is_replaced_by_read_only_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    identity = ProcessIdentity(20, 1.0, "tunnel-client.exe")
    current = RuntimeStatus(
        state=RuntimeState.READY,
        updated_at="now",
        tunnel_state=TunnelState.READY,
        components={
            "tunnel": ComponentStatus(
                component=ComponentName.TUNNEL,
                state=ComponentState.READY,
                owned=True,
                identity=identity,
            )
        },
    )
    monkeypatch.setattr("app.runtime.supervisor.process_is_alive", lambda value: True)
    monkeypatch.setattr(
        "app.runtime.supervisor.CloudflareTunnelProbe.diagnose",
        lambda value: ChatGptTunnelStatus(
            "none",
            TunnelState.NOT_CONFIGURED,
            error_code="persistent_tunnel_not_configured",
        ),
    )
    reconciled = RuntimeController(config)._reconcile_status(current)
    assert reconciled.tunnel_state is TunnelState.NOT_CONFIGURED
    assert reconciled.components["tunnel"].owned is False
    assert reconciled.state is not RuntimeState.READY


def test_stop_protects_against_stale_or_reused_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    stale = ProcessIdentity(81, 1.0, "python.exe")
    status = RuntimeStatus(
        state=RuntimeState.READY,
        updated_at="now",
        components={
            "fastapi": ComponentStatus(
                component=ComponentName.FASTAPI,
                state=ComponentState.READY,
                owned=True,
                identity=stale,
            )
        },
    )
    controller = RuntimeController(config)
    terminated: list[ProcessIdentity] = []
    monkeypatch.setattr("app.runtime.supervisor.process_is_alive", lambda value: False)
    monkeypatch.setattr(
        "app.runtime.supervisor.terminate_verified_process",
        lambda value: terminated.append(value) or False,
    )
    controller._force_stop_owned(status)
    assert terminated == []
    assert controller.status().state is RuntimeState.STOPPED


def test_force_stop_includes_tracked_note_index_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    identity = ProcessIdentity(82, 1.0, str(config.python_exe))
    status = RuntimeStatus(
        state=RuntimeState.STARTING,
        updated_at="now",
        components={
            "zotero_note_index": ComponentStatus(
                component=ComponentName.ZOTERO_NOTE_INDEX,
                state=ComponentState.STARTING,
                owned=True,
                identity=identity,
            )
        },
    )
    terminated: list[ProcessIdentity] = []
    monkeypatch.setattr(
        "app.runtime.supervisor.process_is_alive",
        lambda value: value not in terminated,
    )
    monkeypatch.setattr(
        "app.runtime.supervisor.terminate_verified_process",
        lambda value: terminated.append(value) or True,
    )
    result = RuntimeController(config)._force_stop_owned(status)
    assert terminated == [identity]
    assert result.state is RuntimeState.STOPPED
