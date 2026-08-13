from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import subprocess
from typing import Mapping

from app.runtime.contracts import ProcessIdentity
from app.runtime.pid_identity import (
    ProcessIdentityUnavailable,
    get_process_identity,
    identity_matches,
    process_is_alive,
    terminate_verified_process,
)


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    executable: Path
    arguments: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str] = field(default_factory=dict)
    port: int | None = None


@dataclass
class ManagedProcess:
    spec: ProcessSpec
    identity: ProcessIdentity
    process: subprocess.Popen[bytes] | None = None

    @property
    def pid(self) -> int:
        return self.identity.pid


class ProcessStartError(RuntimeError):
    pass


class ProcessManager:
    def spawn(self, spec: ProcessSpec) -> ManagedProcess:
        executable = _resolve_executable(spec.executable)
        environment = os.environ.copy()
        environment.update({str(key): str(value) for key, value in spec.environment.items()})
        try:
            process = subprocess.Popen(
                [str(executable), *spec.arguments],
                cwd=str(spec.cwd),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                close_fds=True,
                **hidden_windows_subprocess_options(new_process_group=True),
            )
        except OSError as exc:
            raise ProcessStartError(f"unable to start {spec.name}") from exc
        try:
            identity = get_process_identity(process.pid)
        except (OSError, ProcessIdentityUnavailable) as exc:
            _terminate_spawn_handle(process)
            raise ProcessStartError(f"unable to identify {spec.name}") from exc
        return ManagedProcess(spec=spec, identity=identity, process=process)

    def attach(self, spec: ProcessSpec, identity: ProcessIdentity) -> ManagedProcess:
        if not identity_matches(identity):
            raise ProcessStartError(f"stale process identity for {spec.name}")
        executable = _resolve_executable(spec.executable)
        if os.path.normcase(os.path.abspath(identity.executable)) != os.path.normcase(
            os.path.abspath(executable)
        ):
            raise ProcessStartError(f"unexpected process executable for {spec.name}")
        return ManagedProcess(spec=spec, identity=identity)

    def is_alive(self, process: ManagedProcess) -> bool:
        return process_is_alive(process.identity)

    def stop(self, process: ManagedProcess) -> bool:
        return terminate_verified_process(process.identity)


def hidden_windows_subprocess_options(
    *,
    new_process_group: bool = False,
) -> dict[str, object]:
    """Return Windows-only flags that suppress every console host window.

    CREATE_NO_WINDOW is deliberately not combined with DETACHED_PROCESS.  The
    latter makes CREATE_NO_WINDOW ineffective and also weakens lifecycle
    tracking.  A hidden STARTUPINFO is retained as a second Windows-level
    guard for console applications and direct PowerShell probes.
    """

    if os.name != "nt":
        return {}
    creation_flags = subprocess.CREATE_NO_WINDOW
    if new_process_group:
        creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    startup_info = subprocess.STARTUPINFO()
    startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup_info.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": creation_flags,
        "startupinfo": startup_info,
    }


def _resolve_executable(value: Path) -> Path:
    candidate = Path(value)
    if candidate.is_file():
        return candidate.resolve()
    located = shutil.which(str(value))
    if located:
        return Path(located).resolve()
    raise ProcessStartError(f"executable not found: {candidate.name}")


def _terminate_spawn_handle(process: subprocess.Popen[bytes]) -> None:
    """Reap a child whose durable identity could not be established."""

    try:
        process.terminate()
        process.wait(timeout=5)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        # A final wait without a timeout is intentionally avoided; the caller
        # must not hang forever on a broken operating-system process handle.
        pass
