from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from threading import Thread
import time
from typing import Any, BinaryIO, Callable
from uuid import uuid4

from app.runtime.config import RuntimeConfig, atomic_write_json
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
from app.runtime.health import (
    HealthResult,
    check_fastapi_health,
    check_mcp_contract,
    check_mcp_health,
    port_is_listening,
    wait_for_health,
)
from app.runtime.logging import RuntimeMetadataLogger
from app.runtime.pid_identity import (
    ProcessIdentityUnavailable,
    get_loopback_listener_identity,
    get_process_identity,
    process_is_alive,
    terminate_verified_process,
)
from app.runtime.process_manager import (
    ManagedProcess,
    ProcessManager,
    ProcessSpec,
    ProcessStartError,
    hidden_windows_subprocess_options,
)
from app.runtime.tunnel import CloudflareTunnelProbe


class RuntimeStartupError(RuntimeError):
    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


class SingleInstanceLock(AbstractContextManager["SingleInstanceLock"]):
    def __init__(self, path: Path):
        self.path = path
        self._stream: BinaryIO | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self._stream = stream
        return True

    def release(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            raise RuntimeStartupError("launcher_already_running")
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


class ControlRequestQueue:
    """Small, sanitized local queue used by the Zotero integration."""

    def __init__(self, directory: Path):
        self.directory = directory

    def submit(self, request: ControlRequest) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{request.request_id}.json"
        if path.exists():
            raise ValueError("duplicate runtime control request")
        atomic_write_json(path, request.to_dict())
        return path

    def consume(self) -> list[ControlRequest]:
        if not self.directory.is_dir():
            return []
        requests: list[ControlRequest] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                if path.stat().st_size > 4096:
                    raise ValueError("runtime control request is too large")
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("runtime control request must be an object")
                requests.append(ControlRequest.from_dict(value))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass
        return requests


class RuntimeSupervisor:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        process_manager: ProcessManager | None = None,
        logger: RuntimeMetadataLogger | None = None,
    ):
        self.config = config
        self.process_manager = process_manager or ProcessManager()
        self.logger = logger or RuntimeMetadataLogger(config.paths.runtime_log_file)
        self.tunnel_probe = CloudflareTunnelProbe(config)
        self.control_queue = ControlRequestQueue(config.paths.control_dir)
        configured_chat_token = os.environ.get("SEARCH_CHAT_GATEWAY_TOKEN", "").strip()
        self._chat_gateway_token = (
            configured_chat_token
            if len(configured_chat_token) >= 32
            else secrets.token_urlsafe(32)
        )
        self._managed: dict[ComponentName, ManagedProcess] = {}
        self.status = _empty_status(RuntimeState.STOPPED, config=config)

    def supervise_forever(self) -> int:
        self.config.paths.ensure()
        try:
            lock = SingleInstanceLock(self.config.paths.supervisor_lock)
            lock.__enter__()
        except RuntimeStartupError:
            # A second detached launcher can race with the first before the
            # first one persists status.  The lock holder remains authoritative.
            return 0
        try:
            try:
                try:
                    self.config.paths.supervisor_stop_file.unlink()
                except FileNotFoundError:
                    pass
                self._adopt_previous_owned_components()
                self._set_supervisor(ComponentState.STARTING)
                self._persist(RuntimeState.STARTING)
                self.start_components()
                while not self.config.paths.supervisor_stop_file.exists():
                    self._consume_control_requests()
                    self._monitor_once()
                    time.sleep(self.config.monitor_interval_seconds)
            except RuntimeStartupError as exc:
                self._rollback()
                self._safe_persist(RuntimeState.FAILED, error_code=exc.error_code)
                return 1
            except Exception:
                # Do not let a status/log/filesystem failure orphan any child
                # that this supervisor started or safely adopted.
                self._rollback()
                self._safe_persist(
                    RuntimeState.FAILED,
                    error_code="runtime_supervisor_internal_error",
                )
                return 1
            else:
                stopped = self.stop_components()
                self._safe_persist(
                    RuntimeState.STOPPED if stopped else RuntimeState.FAILED,
                    error_code=None if stopped else "runtime_stop_failed",
                )
                return 0 if stopped else 1
            finally:
                try:
                    self.config.paths.supervisor_stop_file.unlink()
                except OSError:
                    pass
        finally:
            lock.release()

    def _adopt_previous_owned_components(self) -> None:
        """Reattach only identities that match this product and current health."""

        path = self.config.paths.status_file
        if not path.is_file():
            return
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return
            previous = RuntimeStatus.from_dict(value)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return
        old_supervisor = previous.components.get(ComponentName.SUPERVISOR.value)
        if (
            old_supervisor
            and old_supervisor.owned
            and old_supervisor.identity
            and process_is_alive(old_supervisor.identity)
        ):
            raise RuntimeStartupError("supervisor_identity_conflict")
        self.status = _apply_runtime_identity(previous, self.config)
        self._adopt_previous_local_component(
            ComponentName.FASTAPI,
            self._fastapi_spec(),
            lambda: check_fastapi_health(self.config.backend_url),
        )
        self._adopt_previous_local_component(
            ComponentName.MCP,
            self._mcp_spec(),
            lambda: check_mcp_contract(self.config.mcp_port),
        )
        # Phase A never inherits authority over a previous launcher process.
        # Historical note-index and tunnel records are discarded without
        # touching their PIDs.  Tunnel state is rebuilt by the read-only
        # Cloudflare probe after the local backends are ready.
        self.status.components.pop(ComponentName.ZOTERO_NOTE_INDEX.value, None)
        self.status.components.pop(ComponentName.TUNNEL.value, None)

    def _stop_previous_note_process(self) -> None:
        previous = self.status.components.get(ComponentName.ZOTERO_NOTE_INDEX.value)
        if not previous or not previous.owned or not previous.identity:
            return
        if not process_is_alive(previous.identity):
            previous.owned = False
            previous.pid = None
            previous.identity = None
            return
        spec = ProcessSpec(
            name="zotero_note_index",
            executable=self.config.python_exe,
            arguments=(),
            cwd=self.config.paths.runtime_root,
        )
        try:
            process = self.process_manager.attach(spec, previous.identity)
        except ProcessStartError as exc:
            raise RuntimeStartupError("zotero_note_index_adoption_rejected") from exc
        self.process_manager.stop(process)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and self.process_manager.is_alive(process):
            time.sleep(0.05)
        if self.process_manager.is_alive(process):
            self._managed[ComponentName.ZOTERO_NOTE_INDEX] = process
            raise RuntimeStartupError("zotero_note_index_previous_process_unhealthy")
        previous.owned = False
        previous.pid = None
        previous.identity = None

    def _adopt_previous_local_component(
        self,
        component: ComponentName,
        spec: ProcessSpec,
        health_check: Callable[[], HealthResult],
    ) -> None:
        previous = self.status.components.get(component.value)
        if not previous or not previous.owned or not previous.identity:
            return
        if not process_is_alive(previous.identity):
            previous.state = ComponentState.STOPPED
            previous.owned = False
            previous.pid = None
            previous.identity = None
            return
        try:
            process = self.process_manager.attach(spec, previous.identity)
        except ProcessStartError:
            # The status file is not authority to claim an arbitrary process.
            previous.state = ComponentState.DEGRADED
            previous.error_code = f"{component.value}_adoption_rejected"
            previous.owned = False
            previous.pid = None
            previous.identity = None
            return
        if health_check().ready:
            self._managed[component] = process
            self._set_owned(component, process, ComponentState.READY)
            return
        # The identity and executable are proven to belong to the previous
        # launcher, so it is safe to stop before starting a replacement.
        self.process_manager.stop(process)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and self.process_manager.is_alive(process):
            time.sleep(0.05)
        if self.process_manager.is_alive(process):
            self._managed[component] = process
            raise RuntimeStartupError(f"{component.value}_previous_owned_unhealthy")
        previous.state = ComponentState.STOPPED
        previous.owned = False
        previous.pid = None
        previous.identity = None

    def start_components(self) -> RuntimeStatus:
        if self.config.mode == "remote":
            raise RuntimeStartupError("remote_mode_does_not_start_local_runtime")
        try:
            self._sync_zotero_retrieval_before_startup()
            self._start_fastapi()
            self._start_mcp()
            # Phase A is deliberately detection-only for ChatGPT tunnels.
            # Never create a random Quick Tunnel or start a named tunnel here.
            self._refresh_tunnel_status()
            self._set_supervisor(ComponentState.READY)
            target_state = self._derive_runtime_state()
            tunnel_status = self.status.components.get(ComponentName.TUNNEL.value)
            self._persist(
                target_state,
                error_code=(
                    tunnel_status.error_code
                    if target_state is RuntimeState.DEGRADED and tunnel_status
                    else None
                ),
            )
            return self.status
        except RuntimeStartupError:
            self._rollback()
            raise

    def _sync_zotero_retrieval_before_startup(self) -> None:
        """Synchronize only while no local retrieval reader is active."""

        if check_fastapi_health(self.config.backend_url).ready:
            return
        if port_is_listening(self.config.backend_port) or port_is_listening(
            self.config.mcp_port
        ):
            # The existing component-start paths own deterministic conflict
            # reporting.  More importantly, never mutate a generation while a
            # process outside this supervisor may still be reading it.
            return
        script = self.config.paths.zotero_retrieval_sync_script
        if not script.is_file():
            raise RuntimeStartupError("zotero_retrieval_sync_script_missing")
        result = self._run_note_index_command(
            script,
            timeout_seconds=7200,
            accept_nonzero_json=True,
        )
        if not result:
            raise RuntimeStartupError("zotero_retrieval_sync_failed")
        if result.get("status") == "error":
            error_code = str(
                result.get("error_code") or "zotero_retrieval_sync_failed"
            )
            raise RuntimeStartupError(error_code)
        safe = bool(
            result.get("status") in {"ready", "unchanged"}
            and result.get("production_db_write_performed") is False
            and result.get("zotero_db_write_performed") is False
            and result.get("pdf_passage_vector_rebuild") is False
            and int(result.get("pdf_passage_embedding_inference_count") or 0)
            == 0
        )
        if not safe:
            raise RuntimeStartupError("zotero_retrieval_sync_contract_failed")
        self.status.components[ComponentName.ZOTERO_NOTE_INDEX.value] = (
            ComponentStatus(
                component=ComponentName.ZOTERO_NOTE_INDEX,
                state=ComponentState.READY,
                error_code=None,
            )
        )

    def stop_components(self) -> bool:
        stopped_all = True
        for component in (
            ComponentName.ZOTERO_NOTE_INDEX,
            ComponentName.MCP,
            ComponentName.FASTAPI,
        ):
            process = self._managed.pop(component, None)
            if process is not None:
                self.process_manager.stop(process)
                deadline = time.monotonic() + 2.0
                while (
                    time.monotonic() < deadline
                    and self.process_manager.is_alive(process)
                ):
                    time.sleep(0.05)
            status = self.status.components.get(component.value)
            if status is not None:
                if component is ComponentName.ZOTERO_NOTE_INDEX and process is None:
                    continue
                if process is None and not status.owned:
                    # A matching pre-existing NOTEBOOK_AI process was reused,
                    # never adopted.  Stopping the launcher must not claim to
                    # have stopped or terminate that external process.
                    status.state = ComponentState.EXTERNAL
                    continue
                if (
                    process is None
                    and status.owned
                    and status.identity
                    and process_is_alive(status.identity)
                ):
                    stopped_all = False
                    status.state = ComponentState.DEGRADED
                    status.error_code = f"{component.value}_ownership_not_attached"
                    continue
                still_alive = bool(
                    process is not None
                    and self.process_manager.is_alive(process)
                )
                if still_alive:
                    stopped_all = False
                    status.state = ComponentState.FAILED
                    status.error_code = f"{component.value}_stop_failed"
                else:
                    status.state = ComponentState.STOPPED
                    status.pid = None
                    status.identity = None
                    status.error_code = None
        supervisor = self.status.components.get(ComponentName.SUPERVISOR.value)
        if supervisor:
            supervisor.state = ComponentState.STOPPED
        return stopped_all

    def _start_fastapi(self) -> None:
        adopted = self._managed.get(ComponentName.FASTAPI)
        if adopted is not None and self.process_manager.is_alive(adopted):
            health = check_fastapi_health(self.config.backend_url)
            if health.ready:
                _apply_backend_readiness(self.status, health)
                self._set_owned(ComponentName.FASTAPI, adopted, ComponentState.READY)
                return
        existing = check_fastapi_health(self.config.backend_url)
        if existing.ready:
            _apply_backend_readiness(self.status, existing)
            self._set_external(ComponentName.FASTAPI, self.config.backend_port)
            return
        if port_is_listening(self.config.backend_port):
            existing = wait_for_health(
                lambda: check_fastapi_health(self.config.backend_url),
                timeout_seconds=min(10.0, self.config.health_timeout_seconds),
                poll_seconds=0.5,
            )
            if existing.ready:
                _apply_backend_readiness(self.status, existing)
                self._set_external(ComponentName.FASTAPI, self.config.backend_port)
                return
            raise RuntimeStartupError(
                _occupied_service_error("backend", existing, "backend_port_conflict")
            )
        spec = self._fastapi_spec()
        self._spawn_and_wait(
            ComponentName.FASTAPI,
            spec,
            lambda: check_fastapi_health(self.config.backend_url),
        )

    def _fastapi_spec(self) -> ProcessSpec:
        import_inbox = str(
            self.config.paths.data_project_root.with_name("search-import-inbox")
        )
        environment = {
            "PYTHONDONTWRITEBYTECODE": "1",
            "SEARCH_CHAT_GATEWAY_TOKEN": self._chat_gateway_token,
            "SEARCH_IMPORT_INBOX": import_inbox,
            "SEARCH_LOG_DIR": str(self.config.paths.logs_dir),
        }
        if self.config.machine_config.path is not None:
            environment["SEARCH_MACHINE_CONFIG_PATH"] = str(self.config.machine_config.path)
        return ProcessSpec(
            name="fastapi",
            executable=self.config.python_exe,
            arguments=(
                "-B",
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.config.backend_port),
            ),
            cwd=self.config.paths.runtime_root,
            environment=environment,
            port=self.config.backend_port,
        )

    def _start_mcp(self) -> None:
        if not self.config.paths.mcp_server_entry.is_file():
            raise RuntimeStartupError("mcp_build_missing")
        adopted = self._managed.get(ComponentName.MCP)
        if adopted is not None and self.process_manager.is_alive(adopted):
            health = check_mcp_contract(self.config.mcp_port)
            if health.ready:
                self.status.mcp_ready = self.status.retrieval_ready
                self._set_owned(ComponentName.MCP, adopted, ComponentState.READY)
                return
        existing = check_mcp_contract(self.config.mcp_port)
        if existing.ready:
            self.status.mcp_ready = self.status.retrieval_ready
            self._set_external(ComponentName.MCP, self.config.mcp_port)
            return
        if port_is_listening(self.config.mcp_port):
            existing = wait_for_health(
                lambda: check_mcp_contract(self.config.mcp_port),
                timeout_seconds=min(5.0, self.config.health_timeout_seconds),
                poll_seconds=0.5,
            )
            if existing.ready:
                self.status.mcp_ready = self.status.retrieval_ready
                self._set_external(ComponentName.MCP, self.config.mcp_port)
                return
            raise RuntimeStartupError(
                _occupied_service_error("mcp", existing, "mcp_port_conflict")
            )
        spec = self._mcp_spec()
        self._spawn_and_wait(
            ComponentName.MCP,
            spec,
            lambda: check_mcp_contract(self.config.mcp_port),
        )

    def _mcp_spec(self) -> ProcessSpec:
        import_inbox = str(
            self.config.paths.data_project_root.with_name("search-import-inbox")
        )
        environment = {
            "SEARCH_BACKEND_URL": self.config.backend_url,
            "SEARCH_BACKEND_BEARER_TOKEN": self._chat_gateway_token,
            "SEARCH_CHAT_GATEWAY_TOKEN": self._chat_gateway_token,
            "SEARCH_IMPORT_INBOX": import_inbox,
            "SEARCH_ALLOW_UNAUTHENTICATED_MCP_DEV": "1",
            "SEARCH_LOG_DIR": str(self.config.paths.logs_dir),
            "SEARCH_MCP_PORT": str(self.config.mcp_port),
            "NOTEBOOK_AI_BACKEND_URL": self.config.backend_url,
            "NOTEBOOK_AI_ALLOW_UNAUTHENTICATED_MCP_DEV": "1",
            "NOTEBOOK_AI_MCP_PORT": str(self.config.mcp_port),
        }
        if self.config.machine_config.path is not None:
            environment["SEARCH_MACHINE_CONFIG_PATH"] = str(self.config.machine_config.path)
        return ProcessSpec(
            name="mcp",
            executable=self.config.node_exe,
            arguments=(str(self.config.paths.mcp_server_entry),),
            cwd=self.config.paths.mcp_app_dir,
            environment=environment,
            port=self.config.mcp_port,
        )

    def _spawn_and_wait(
        self,
        component: ComponentName,
        spec: ProcessSpec,
        health_check: Callable[[], Any],
    ) -> None:
        started = time.monotonic()
        try:
            process = self.process_manager.spawn(spec)
        except ProcessStartError as exc:
            raise RuntimeStartupError(f"{component.value}_start_failed") from exc
        self._managed[component] = process
        self._set_owned(component, process, ComponentState.STARTING)
        result = wait_for_health(
            health_check,
            timeout_seconds=self.config.health_timeout_seconds,
            process_alive=lambda: self.process_manager.is_alive(process),
        )
        if not result.ready:
            stopped = self._stop_managed_process(process)
            if stopped:
                self._managed.pop(component, None)
            failed = self.status.components.get(component.value)
            if failed is not None:
                failed.state = ComponentState.FAILED
                failed.error_code = (
                    f"{component.value}_{result.error_code or 'health_failed'}"
                    if stopped
                    else f"{component.value}_stop_failed"
                )
            raise RuntimeStartupError(
                (
                    f"{component.value}_{result.error_code or 'health_failed'}"
                    if stopped
                    else f"{component.value}_stop_failed"
                )
            )
        self._set_owned(component, process, ComponentState.READY)
        if component is ComponentName.FASTAPI:
            _apply_backend_readiness(self.status, result)
        elif component is ComponentName.MCP:
            self.status.mcp_ready = self.status.retrieval_ready
        self.logger.log(
            component=component.value,
            state=ComponentState.READY.value,
            pid=process.pid,
            port=spec.port,
            duration=time.monotonic() - started,
        )

    def _monitor_once(self) -> None:
        for component, process in tuple(self._managed.items()):
            alive = self.process_manager.is_alive(process)
            health_error: str | None = None
            if alive and component is ComponentName.FASTAPI:
                health = check_fastapi_health(self.config.backend_url)
                _apply_backend_readiness(self.status, health)
                if not health.ready and port_is_listening(self.config.backend_port):
                    current = self.status.components.get(component.value)
                    if current:
                        current.state = ComponentState.DEGRADED
                        current.error_code = health.error_code or "backend_health_failed"
                    continue
                alive = health.ready
                health_error = health.error_code
            elif alive and component is ComponentName.MCP:
                health = check_mcp_contract(self.config.mcp_port)
                self.status.mcp_ready = health.ready and self.status.retrieval_ready
                if not health.ready and port_is_listening(self.config.mcp_port):
                    current = self.status.components.get(component.value)
                    if current:
                        current.state = ComponentState.DEGRADED
                        current.error_code = health.error_code or "mcp_contract_failed"
                    continue
                alive = health.ready
                health_error = health.error_code
            if alive:
                continue
            current = self.status.components.get(component.value)
            restart_count = (current.restart_count if current else 0) + 1
            if self.process_manager.is_alive(process):
                if not self._stop_managed_process(process):
                    if current:
                        current.state = ComponentState.FAILED
                        current.error_code = f"{component.value}_stop_failed"
                    self._persist(
                        RuntimeState.DEGRADED,
                        error_code=f"{component.value}_stop_failed",
                    )
                    continue
            if restart_count > self.config.max_restart_count:
                self._managed.pop(component, None)
                if current:
                    current.state = ComponentState.FAILED
                    current.error_code = f"{component.value}_restart_exhausted"
                    current.restart_count = restart_count
                self._persist(
                    RuntimeState.FAILED,
                    error_code=f"{component.value}_restart_exhausted",
                )
                continue
            self.logger.log(
                component=component.value,
                state=ComponentState.DEGRADED.value,
                error_code="child_exited" if health_error is None else "health_failed",
                restart_count=restart_count,
            )
            time.sleep(min(30.0, float(2 ** (restart_count - 1))))
            self._managed.pop(component, None)
            try:
                if component is ComponentName.FASTAPI:
                    self._start_fastapi()
                elif component is ComponentName.MCP:
                    self._start_mcp()
                replacement = self.status.components.get(component.value)
                if replacement:
                    replacement.restart_count = restart_count
            except RuntimeStartupError as exc:
                replacement_process = self._managed.pop(component, None)
                replacement_stopped = True
                if replacement_process is not None:
                    replacement_stopped = self._stop_managed_process(
                        replacement_process
                    )
                replacement = self.status.components.get(component.value) or current
                if replacement:
                    replacement.state = ComponentState.DEGRADED
                    replacement.error_code = (
                        exc.error_code
                        if replacement_stopped
                        else f"{component.value}_stop_failed"
                    )
                    replacement.restart_count = restart_count
                    self.status.components[component.value] = replacement
                # Keep a dead identity as a retry sentinel.  It cannot be
                # terminated as a different process because every operation
                # revalidates creation time and executable path.
                self._managed[component] = (
                    process if replacement_stopped else replacement_process
                )
                self._persist(RuntimeState.DEGRADED, error_code=exc.error_code)
        self._monitor_external_components()
        self._refresh_tunnel_status()
        derived = self._derive_runtime_state()
        active_error = next(
            (
                component.error_code
                for component in self.status.components.values()
                if component.error_code
                and component.state in {ComponentState.DEGRADED, ComponentState.FAILED}
            ),
            None,
        )
        self._persist(derived, error_code=active_error)

    def _stop_managed_process(
        self,
        process: ManagedProcess,
        *,
        timeout_seconds: float = 5.0,
    ) -> bool:
        self.process_manager.stop(process)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and self.process_manager.is_alive(process):
            time.sleep(0.05)
        return not self.process_manager.is_alive(process)

    def _monitor_external_components(self) -> None:
        for component, port, health_check in (
            (
                ComponentName.FASTAPI,
                self.config.backend_port,
                lambda: check_fastapi_health(self.config.backend_url),
            ),
            (
                ComponentName.MCP,
                self.config.mcp_port,
                lambda: check_mcp_health(self.config.mcp_port),
            ),
        ):
            current = self.status.components.get(component.value)
            if current is None or current.owned:
                continue
            health = health_check()
            if component is ComponentName.FASTAPI:
                _apply_backend_readiness(self.status, health)
            elif component is ComponentName.MCP:
                self.status.mcp_ready = health.ready and self.status.retrieval_ready
            if health.ready:
                current.state = ComponentState.EXTERNAL
                current.error_code = None
                current.restart_count = 0
                continue
            if port_is_listening(port):
                current.state = ComponentState.DEGRADED
                current.error_code = (
                    "backend_port_conflict"
                    if component is ComponentName.FASTAPI
                    else "mcp_port_conflict"
                )
                continue
            restart_count = current.restart_count + 1
            current.restart_count = restart_count
            if restart_count > self.config.max_restart_count:
                current.state = ComponentState.FAILED
                current.error_code = f"{component.value}_restart_exhausted"
                continue
            time.sleep(min(30.0, float(2 ** (restart_count - 1))))
            try:
                if component is ComponentName.FASTAPI:
                    self._start_fastapi()
                else:
                    self._start_mcp()
                replacement = self.status.components.get(component.value)
                if replacement:
                    replacement.restart_count = restart_count
            except RuntimeStartupError as exc:
                current.state = ComponentState.DEGRADED
                current.error_code = exc.error_code

    def _consume_control_requests(self) -> None:
        for request in self.control_queue.consume():
            if request.action == "sync_zotero_notes":
                self._sync_note_index_while_running()
            elif request.action == "restart":
                if not self.stop_components():
                    raise RuntimeStartupError("runtime_stop_failed")
                self._set_supervisor(ComponentState.STARTING)
                self._persist(RuntimeState.STARTING)
                self.start_components()

    def _sync_note_index_while_running(self) -> None:
        try:
            self._ensure_note_index(sync_if_missing=True, force_sync=True)
        except Exception:
            self.status.components[ComponentName.ZOTERO_NOTE_INDEX.value] = (
                ComponentStatus(
                    component=ComponentName.ZOTERO_NOTE_INDEX,
                    state=ComponentState.DEGRADED,
                    error_code="zotero_note_index_sync_failed",
                )
            )
            self.logger.log(
                component=ComponentName.ZOTERO_NOTE_INDEX.value,
                state=ComponentState.DEGRADED.value,
                error_code="zotero_note_index_sync_failed",
            )
            self._safe_persist(
                RuntimeState.DEGRADED,
                error_code="zotero_note_index_sync_failed",
            )

    def _ensure_note_index(
        self,
        *,
        sync_if_missing: bool,
        force_sync: bool = False,
        incremental_sync: bool = False,
    ) -> None:
        status = self._run_note_index_command(
            self.config.paths.note_status_script,
            timeout_seconds=30,
        )
        ready = bool(status and status.get("status") == "ready")
        if force_sync or incremental_sync or (sync_if_missing and not ready):
            synced = self._run_note_index_command(
                self.config.paths.note_sync_script,
                timeout_seconds=1800,
            )
            sync_safe = bool(
                synced
                and synced.get("status") == "ready"
                and synced.get("production_db_write_performed") is False
                and synced.get("zotero_db_write_performed") is False
            )
            if not sync_safe:
                raise RuntimeStartupError("zotero_note_index_sync_failed")
            status = self._run_note_index_command(
                self.config.paths.note_status_script,
                timeout_seconds=30,
            )
            ready = bool(status and status.get("status") == "ready")
        state = ComponentState.READY if ready else ComponentState.FAILED
        self.status.components[ComponentName.ZOTERO_NOTE_INDEX.value] = ComponentStatus(
            component=ComponentName.ZOTERO_NOTE_INDEX,
            state=state,
            error_code=None if ready else "zotero_note_index_not_ready",
        )
        if not ready:
            raise RuntimeStartupError("zotero_note_index_not_ready")

    def _run_note_index_command(
        self,
        script: Path,
        *,
        timeout_seconds: float,
        accept_nonzero_json: bool = False,
    ) -> dict[str, Any] | None:
        environment = os.environ.copy()
        environment["SEARCH_DATA_DIR"] = str(self.config.paths.data_dir)
        if self.config.machine_config.path is not None:
            environment["SEARCH_MACHINE_CONFIG_PATH"] = str(
                self.config.machine_config.path
            )
        try:
            process = subprocess.Popen(
                [str(self.config.python_exe), "-B", str(script)],
                cwd=str(self.config.paths.runtime_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=environment,
                shell=False,
                **hidden_windows_subprocess_options(),
            )
        except OSError:
            return None
        stdout_buffer = bytearray()
        stdout_overflow = [False]

        def drain_stdout() -> None:
            stream = process.stdout
            if stream is None:
                return
            while True:
                chunk = stream.read(65_536)
                if not chunk:
                    return
                remaining = 1_048_577 - len(stdout_buffer)
                if remaining > 0:
                    stdout_buffer.extend(chunk[:remaining])
                if len(chunk) > remaining or len(stdout_buffer) > 1_048_576:
                    stdout_overflow[0] = True

        stdout_reader = Thread(
            target=drain_stdout,
            name="notebook-ai-note-index-output",
            daemon=True,
        )
        stdout_reader.start()
        try:
            identity = get_process_identity(process.pid)
        except (OSError, ProcessIdentityUnavailable):
            _terminate_popen_handle(process)
            return None
        managed = ManagedProcess(
            ProcessSpec(
                name="zotero_note_index",
                executable=self.config.python_exe,
                arguments=("-B", str(script)),
                cwd=self.config.paths.runtime_root,
            ),
            identity,
            process,
        )
        self._managed[ComponentName.ZOTERO_NOTE_INDEX] = managed
        self._set_owned(
            ComponentName.ZOTERO_NOTE_INDEX,
            managed,
            ComponentState.STARTING,
        )
        try:
            self._persist(self.status.state, error_code=self.status.error_code)
        except Exception:
            _terminate_popen_handle(process)
            self._managed.pop(ComponentName.ZOTERO_NOTE_INDEX, None)
            raise
        deadline = time.monotonic() + timeout_seconds
        next_heartbeat = time.monotonic() + 10.0
        try:
            while process.poll() is None:
                now = time.monotonic()
                if now >= deadline:
                    _terminate_popen_handle(process)
                    return None
                if now >= next_heartbeat:
                    self._safe_persist(self.status.state, error_code=self.status.error_code)
                    next_heartbeat = now + 10.0
                time.sleep(0.25)
            stdout_reader.join(timeout=5)
            if stdout_reader.is_alive():
                return None
            stdout = bytes(stdout_buffer)
        finally:
            self._managed.pop(ComponentName.ZOTERO_NOTE_INDEX, None)
            current = self.status.components.get(ComponentName.ZOTERO_NOTE_INDEX.value)
            if current and current.identity == identity:
                current.owned = False
                current.pid = None
                current.identity = None
            self._safe_persist(self.status.state, error_code=self.status.error_code)
        if stdout_overflow[0] or len(stdout) > 1_048_576:
            return None
        try:
            value = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if process.returncode != 0 and not accept_nonzero_json:
            return None
        return value if isinstance(value, dict) else None

    def _set_supervisor(self, state: ComponentState) -> None:
        try:
            identity = get_process_identity(os.getpid())
        except ProcessIdentityUnavailable:
            identity = ProcessIdentity(
                pid=os.getpid(),
                creation_time=time.time(),
                executable=sys.executable,
            )
        self.status.components[ComponentName.SUPERVISOR.value] = ComponentStatus(
            component=ComponentName.SUPERVISOR,
            state=state,
            pid=identity.pid,
            owned=True,
            identity=identity,
        )

    def _set_owned(
        self,
        component: ComponentName,
        process: ManagedProcess,
        state: ComponentState,
    ) -> None:
        previous = self.status.components.get(component.value)
        self.status.components[component.value] = ComponentStatus(
            component=component,
            state=state,
            pid=process.pid,
            port=process.spec.port,
            restart_count=(
                previous.restart_count
                if previous and previous.state is not ComponentState.STOPPED
                else 0
            ),
            owned=True,
            identity=process.identity,
        )

    def _set_external(self, component: ComponentName, port: int) -> None:
        identity = get_loopback_listener_identity(port)
        self.status.components[component.value] = ComponentStatus(
            component=component,
            state=ComponentState.EXTERNAL,
            pid=identity.pid if identity else None,
            port=port,
            owned=False,
            identity=identity,
        )

    def _rollback(self) -> None:
        managed_snapshot = tuple(self._managed.items())
        try:
            self.stop_components()
        except Exception:
            for component, process in managed_snapshot:
                try:
                    self._stop_managed_process(process)
                except Exception:
                    pass
                finally:
                    self._managed.pop(component, None)

    def _derive_runtime_state(self) -> RuntimeState:
        local = [
            self.status.components.get(ComponentName.FASTAPI.value),
            self.status.components.get(ComponentName.MCP.value),
        ]
        if any(item is None for item in local):
            return RuntimeState.DEGRADED
        if any(item and item.state is ComponentState.FAILED for item in local):
            return RuntimeState.FAILED
        if any(item and item.state is ComponentState.DEGRADED for item in local):
            return RuntimeState.DEGRADED
        if any(
            item
            and item.state
            not in {ComponentState.READY, ComponentState.EXTERNAL}
            for item in local
        ):
            return RuntimeState.STARTING
        note_index = self.status.components.get(ComponentName.ZOTERO_NOTE_INDEX.value)
        if note_index and note_index.state in {
            ComponentState.DEGRADED,
            ComponentState.FAILED,
        }:
            return RuntimeState.DEGRADED
        return (
            RuntimeState.READY
            if self.status.tunnel_state is TunnelState.PERSISTENT_ONLINE
            else RuntimeState.LOCAL_READY_TUNNEL_MISSING
        )

    def _refresh_tunnel_status(self) -> None:
        _apply_chatgpt_tunnel_status(self.status, self.tunnel_probe.diagnose())

    def _persist(
        self,
        state: RuntimeState,
        *,
        error_code: str | None = None,
    ) -> None:
        self.status.state = state
        if state is RuntimeState.STOPPED:
            _clear_runtime_readiness(self.status)
        _apply_runtime_identity(self.status, self.config)
        self.status.updated_at = _utc_now()
        self.status.error_code = error_code
        atomic_write_json(self.config.paths.status_file, self.status.to_dict())

    def _safe_persist(
        self,
        state: RuntimeState,
        *,
        error_code: str | None = None,
    ) -> None:
        try:
            self._persist(state, error_code=error_code)
        except (OSError, ValueError):
            return


class RuntimeController:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        process_manager: ProcessManager | None = None,
    ):
        self.config = config
        self.process_manager = process_manager or ProcessManager()
        self.control_queue = ControlRequestQueue(config.paths.control_dir)

    def start(self, *, wait_seconds: float = 60.0) -> RuntimeStatus:
        existing = self.status()
        supervisor = existing.components.get(ComponentName.SUPERVISOR.value)
        if supervisor and supervisor.identity and process_is_alive(supervisor.identity):
            return existing
        if self.config.mode == "remote":
            raise RuntimeStartupError("remote_mode_does_not_start_local_runtime")
        self.config.paths.ensure()
        try:
            self.config.paths.supervisor_stop_file.unlink()
        except FileNotFoundError:
            pass
        supervisor_arguments = [
            "-B",
            str(self.config.paths.launcher_script),
        ]
        if self.config.machine_config.path is not None:
            supervisor_arguments.extend(["--machine-config", str(self.config.machine_config.path)])
        supervisor_arguments.append("supervise")
        spec = ProcessSpec(
            name="supervisor",
            executable=self.config.python_exe,
            arguments=tuple(supervisor_arguments),
            cwd=self.config.paths.runtime_root,
            environment={
                "PYTHONDONTWRITEBYTECODE": "1",
                "SEARCH_CHAT_GATEWAY_TOKEN": secrets.token_urlsafe(32),
            },
        )
        try:
            self.process_manager.spawn(spec)
        except ProcessStartError as exc:
            raise RuntimeStartupError("supervisor_start_failed") from exc
        deadline = time.monotonic() + wait_seconds
        last = existing
        while time.monotonic() < deadline:
            time.sleep(0.25)
            # The supervisor performs the full index/tool/widget readiness
            # contract before persisting a terminal state.  Poll the persisted
            # status here instead of issuing overlapping expensive health
            # requests every 250 ms during startup.
            last = self._read_persisted_status()
            if last.state not in {RuntimeState.STOPPED, RuntimeState.STARTING}:
                return last
        supervisor = last.components.get(ComponentName.SUPERVISOR.value)
        if (
            last.state is RuntimeState.STARTING
            and supervisor
            and supervisor.identity
            and process_is_alive(supervisor.identity)
        ):
            # The live supervisor can still be inside bounded backend health
            # checks; do not misreport a start failure while its identity is
            # valid and status heartbeats continue.
            return last
        raise RuntimeStartupError("launcher_start_timeout")

    def stop(self, *, wait_seconds: float = 15.0) -> RuntimeStatus:
        current = self.status()
        supervisor = current.components.get(ComponentName.SUPERVISOR.value)
        if not supervisor or not supervisor.identity or not process_is_alive(supervisor.identity):
            return self._force_stop_owned(current)
        self.config.paths.ensure()
        atomic_write_json(
            self.config.paths.supervisor_stop_file,
            {"action": "stop", "timestamp": _utc_now()},
        )
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline and process_is_alive(supervisor.identity):
            time.sleep(0.25)
        if process_is_alive(supervisor.identity):
            terminate_verified_process(supervisor.identity)
        return self._force_stop_owned(self.status())

    def restart(self) -> RuntimeStatus:
        stopped = self.stop()
        if stopped.state is not RuntimeState.STOPPED:
            raise RuntimeStartupError("runtime_stop_failed")
        return self.start()

    def status(self) -> RuntimeStatus:
        current = self._read_persisted_status()
        if current.state is RuntimeState.FAILED and current.error_code == "runtime_status_invalid":
            return current
        return self._reconcile_status(current)

    def _read_persisted_status(self) -> RuntimeStatus:
        path = self.config.paths.status_file
        if not path.is_file():
            return _empty_status(RuntimeState.STOPPED, config=self.config)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            return _apply_runtime_identity(RuntimeStatus.from_dict(value), self.config)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return _empty_status(
                RuntimeState.FAILED,
                config=self.config,
                error_code="runtime_status_invalid",
            )

    def signal(self, action: str, *, request_id: str | None = None) -> Path:
        request = ControlRequest(
            action=action,  # type: ignore[arg-type]
            request_id=request_id or uuid4().hex,
            timestamp=_utc_now(),
        )
        return self.control_queue.submit(request)

    def doctor(self) -> dict[str, Any]:
        return {
            "mode": self.config.mode,
            "python_ready": self.config.python_exe.is_file(),
            "node_ready": self.config.node_exe.is_file()
            or bool(_which_path(self.config.node_exe)),
            "mcp_build_ready": self.config.paths.mcp_server_entry.is_file(),
            "fastapi_health": check_fastapi_health(self.config.backend_url).ready,
            "mcp_health": check_mcp_contract(self.config.mcp_port).ready,
            "tunnel": self.tunnel_status(run_doctor=True),
            "runtime_state": self.status().state.value,
        }

    def tunnel_status(self, *, run_doctor: bool) -> dict[str, object]:
        del run_doctor
        return CloudflareTunnelProbe(self.config).diagnose().to_dict()

    def _reconcile_status(self, current: RuntimeStatus) -> RuntimeStatus:
        if current.state is RuntimeState.STOPPED:
            return current
        supervisor = current.components.get(ComponentName.SUPERVISOR.value)
        supervisor_alive = bool(
            supervisor
            and supervisor.owned
            and supervisor.identity
            and process_is_alive(supervisor.identity)
        )
        if current.state is RuntimeState.STARTING and supervisor_alive:
            return current
        local_ready = True
        owned_child_alive = False
        for component_name, health_check in (
            (
                ComponentName.FASTAPI,
                lambda: check_fastapi_health(self.config.backend_url),
            ),
            (
                ComponentName.MCP,
                lambda: check_mcp_contract(self.config.mcp_port),
            ),
        ):
            component = current.components.get(component_name.value)
            if component is None:
                local_ready = False
                continue
            identity_alive = bool(
                component.owned
                and component.identity
                and process_is_alive(component.identity)
            )
            owned_child_alive = owned_child_alive or identity_alive
            if component.owned and not identity_alive:
                component.state = ComponentState.FAILED
                component.error_code = f"{component_name.value}_identity_stale"
                local_ready = False
                continue
            health = health_check()
            if component_name is ComponentName.FASTAPI:
                _apply_backend_readiness(current, health)
            elif component_name is ComponentName.MCP:
                current.mcp_ready = health.ready and current.retrieval_ready
            if not health.ready:
                component.state = ComponentState.DEGRADED
                component.error_code = f"{component_name.value}_health_failed"
                local_ready = False
            elif not component.owned:
                component.state = ComponentState.EXTERNAL
                component.error_code = None
            else:
                component.state = ComponentState.READY
                component.error_code = None

        _apply_chatgpt_tunnel_status(
            current,
            CloudflareTunnelProbe(self.config).diagnose(),
        )

        if supervisor and supervisor.owned and not supervisor_alive:
            supervisor.state = ComponentState.FAILED
            supervisor.error_code = "supervisor_identity_stale"
            if current.state is RuntimeState.FAILED:
                return current
            if owned_child_alive:
                current.state = RuntimeState.DEGRADED
                current.error_code = "supervisor_not_running"
            elif not owned_child_alive:
                current.state = RuntimeState.STOPPED
                current.error_code = None
        note_index = current.components.get(ComponentName.ZOTERO_NOTE_INDEX.value)
        if supervisor and supervisor.owned and not supervisor_alive:
            pass
        elif not local_ready:
            current.state = RuntimeState.DEGRADED
            current.error_code = "local_health_failed"
        elif note_index and note_index.state in {
            ComponentState.DEGRADED,
            ComponentState.FAILED,
        }:
            current.state = RuntimeState.DEGRADED
            current.error_code = note_index.error_code
        elif current.tunnel_state is TunnelState.PERSISTENT_ONLINE:
            current.state = RuntimeState.READY
            current.error_code = None
        else:
            current.state = RuntimeState.LOCAL_READY_TUNNEL_MISSING
            current.error_code = None
        return current

    def _force_stop_owned(self, current: RuntimeStatus) -> RuntimeStatus:
        stop_failed = False
        for name in (
            ComponentName.ZOTERO_NOTE_INDEX.value,
            ComponentName.MCP.value,
            ComponentName.FASTAPI.value,
            ComponentName.SUPERVISOR.value,
        ):
            component = current.components.get(name)
            if component and component.owned and component.identity:
                if process_is_alive(component.identity):
                    terminate_verified_process(component.identity)
                    deadline = time.monotonic() + 2.0
                    while time.monotonic() < deadline and process_is_alive(component.identity):
                        time.sleep(0.05)
                if process_is_alive(component.identity):
                    component.state = ComponentState.FAILED
                    component.error_code = f"{name}_stop_failed"
                    stop_failed = True
                    continue
            if component:
                component.state = ComponentState.STOPPED
                component.pid = None
                component.identity = None
                component.error_code = None
        current.state = RuntimeState.FAILED if stop_failed else RuntimeState.STOPPED
        current.updated_at = _utc_now()
        current.error_code = "runtime_stop_failed" if stop_failed else None
        _apply_runtime_identity(current, self.config)
        self.config.paths.ensure()
        atomic_write_json(self.config.paths.status_file, current.to_dict())
        return current


def _empty_status(
    state: RuntimeState,
    *,
    config: RuntimeConfig | None = None,
    error_code: str | None = None,
) -> RuntimeStatus:
    status = RuntimeStatus(
        state=state,
        updated_at=_utc_now(),
        error_code=error_code,
    )
    return _apply_runtime_identity(status, config) if config else status


def _apply_runtime_identity(
    status: RuntimeStatus,
    config: RuntimeConfig,
) -> RuntimeStatus:
    identity = config.build_identity
    status.product = identity.product
    status.version = identity.version
    status.build_id = identity.build_id
    status.source_commit = identity.source_commit
    status.source_branch = identity.source_branch
    status.data_root = str(config.paths.data_dir)
    status.machine_config_status = config.machine_config.status
    status.machine_config_error_code = config.machine_config.error_code
    return status


def _apply_backend_readiness(status: RuntimeStatus, result: HealthResult) -> None:
    details = result.details or {}
    status.api_ready = details.get("api_ready") is True
    status.retrieval_ready = details.get("retrieval_ready") is True
    status.model_state = _safe_model_state(details.get("model_state"))
    status.embedding_state = _safe_model_state(details.get("embedding_state"))
    status.reranker_state = _safe_model_state(details.get("reranker_state"))
    error_code = details.get("last_model_error_code")
    status.last_model_error_code = (
        str(error_code)
        if isinstance(error_code, str)
        and error_code
        and len(error_code) <= 96
        and all(character.isalnum() or character in "_.-" for character in error_code)
        else None
    )
    changed = details.get("last_state_change")
    status.last_state_change = str(changed)[:64] if isinstance(changed, str) else None
    status.embedding_model_ready = status.embedding_state == "ready"
    status.reranker_model_ready = status.reranker_state == "ready"


def _clear_runtime_readiness(status: RuntimeStatus) -> None:
    status.api_ready = False
    status.retrieval_ready = False
    status.mcp_ready = False
    status.model_state = "unconfigured"
    status.embedding_state = "unconfigured"
    status.reranker_state = "unconfigured"
    status.last_model_error_code = None
    status.last_state_change = None
    status.embedding_model_ready = False
    status.reranker_model_ready = False


def _safe_model_state(value: Any) -> str:
    return str(value) if value in {"unconfigured", "loading", "ready", "failed", "recovering"} else "unconfigured"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _which_path(executable: Path) -> str | None:
    import shutil

    return shutil.which(str(executable))


def _occupied_service_error(
    component: str,
    result: HealthResult,
    fallback: str,
) -> str:
    code = str(result.error_code or "")
    if not code:
        return fallback
    if code.startswith(f"{component}_") or code.startswith("backend_"):
        return code
    safe = code if all(character.isalnum() or character in "_.-" for character in code) else "unhealthy"
    return f"{component}_{safe}"


def _apply_chatgpt_tunnel_status(status: RuntimeStatus, diagnosis: Any) -> None:
    status.tunnel_state = diagnosis.state
    status.tunnel_type = diagnosis.tunnel_type
    status.tunnel_url = diagnosis.public_url
    status.tunnel_config_path = diagnosis.config_path
    status.tunnel_credentials_present = diagnosis.credentials_present
    ready = diagnosis.state in {
        TunnelState.QUICK_ONLINE,
        TunnelState.PERSISTENT_ONLINE,
    }
    status.components[ComponentName.TUNNEL.value] = ComponentStatus(
        component=ComponentName.TUNNEL,
        state=ComponentState.EXTERNAL if ready else ComponentState.DEGRADED,
        pid=diagnosis.pid,
        error_code=diagnosis.error_code,
        owned=False,
    )


def _terminate_popen_handle(process: subprocess.Popen[bytes]) -> None:
    try:
        process.terminate()
        process.wait(timeout=5)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return
