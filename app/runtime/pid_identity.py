from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path

from app.runtime.contracts import ProcessIdentity


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x00000102
STILL_ACTIVE = 259
_WINDOWS_EPOCH_SECONDS = 11_644_473_600


class ProcessIdentityUnavailable(RuntimeError):
    pass


def get_process_identity(pid: int) -> ProcessIdentity:
    """Read immutable identity fields used to defend against PID reuse."""

    if pid <= 0:
        raise ProcessIdentityUnavailable("invalid process id")
    if os.name != "nt":
        return _get_posix_identity(pid)
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
        False,
        pid,
    )
    if not handle:
        raise ProcessIdentityUnavailable("process is not available")
    try:
        return _get_windows_identity_from_handle(handle, pid)
    finally:
        kernel32.CloseHandle(handle)


def identity_matches(
    expected: ProcessIdentity,
    actual: ProcessIdentity | None = None,
    *,
    creation_time_tolerance: float = 0.01,
) -> bool:
    try:
        observed = actual or get_process_identity(expected.pid)
    except ProcessIdentityUnavailable:
        return False
    return (
        observed.pid == expected.pid
        and abs(observed.creation_time - expected.creation_time)
        <= creation_time_tolerance
        and _normalized_executable(observed.executable)
        == _normalized_executable(expected.executable)
    )


def process_is_alive(identity: ProcessIdentity) -> bool:
    return identity_matches(identity)


def terminate_verified_process(identity: ProcessIdentity, exit_code: int = 0) -> bool:
    """Terminate only after PID, creation time, and executable all match."""

    if os.name != "nt":
        if not identity_matches(identity):
            return False
        try:
            os.kill(identity.pid, 15)
            return True
        except OSError:
            return False
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
        False,
        identity.pid,
    )
    if not handle:
        return False
    try:
        try:
            observed = _get_windows_identity_from_handle(handle, identity.pid)
        except ProcessIdentityUnavailable:
            return False
        if not identity_matches(identity, observed):
            return False
        return bool(kernel32.TerminateProcess(handle, exit_code))
    finally:
        kernel32.CloseHandle(handle)


def _normalized_executable(value: str) -> str:
    return os.path.normcase(os.path.abspath(value))


def _get_windows_identity_from_handle(
    handle: wintypes.HANDLE,
    pid: int,
) -> ProcessIdentity:
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    kernel32 = _kernel32()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        raise ProcessIdentityUnavailable("process creation time is unavailable")
    size = wintypes.DWORD(32_768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        raise ProcessIdentityUnavailable("process executable is unavailable")
    ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    return ProcessIdentity(
        pid=pid,
        creation_time=(ticks / 10_000_000) - _WINDOWS_EPOCH_SECONDS,
        executable=str(Path(buffer.value).resolve()),
    )


def _kernel32() -> ctypes.WinDLL:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    return kernel32


def _get_posix_identity(pid: int) -> ProcessIdentity:
    proc = Path("/proc") / str(pid)
    try:
        executable = str((proc / "exe").resolve(strict=True))
        stat = (proc / "stat").read_text(encoding="utf-8").split()
        boot_time = 0.0
        for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                boot_time = float(line.split()[1])
                break
        ticks = os.sysconf("SC_CLK_TCK")
        creation_time = boot_time + (float(stat[21]) / float(ticks))
    except (OSError, ValueError, IndexError) as exc:
        raise ProcessIdentityUnavailable("process identity is unavailable") from exc
    return ProcessIdentity(pid=pid, creation_time=creation_time, executable=executable)
