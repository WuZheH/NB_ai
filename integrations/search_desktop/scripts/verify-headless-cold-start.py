from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from typing import Any
from urllib.request import urlopen

import psutil


FORBIDDEN_WINDOW_CLASSES = {
    "consolewindowclass",
    "cascadia_hosting_window_class",
    "pseudoconsolewindow",
}
FORBIDDEN_PROCESS_NAMES = {
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "python.exe",
    "pythonw.exe",
    "windowsterminal.exe",
}


def main() -> int:
    arguments = parse_arguments()
    if os.name != "nt":
        raise RuntimeError("headless_cold_start_windows_only")
    executable = arguments.executable.resolve(strict=True)
    data_project_root = arguments.project_root.resolve(strict=True)
    runtime_root = (
        executable.parent / "resources" / "app" / "runtime-project"
    ).resolve(strict=True)
    python_exe = arguments.python_exe.resolve(strict=True)
    node_exe = arguments.node_exe.resolve(strict=True)
    test_root = arguments.test_root.resolve()
    ensure_port_available(arguments.backend_port)
    ensure_port_available(arguments.mcp_port)

    local_app_data = test_root / "local-app-data"
    roaming_app_data = test_root / "roaming-app-data"
    temp_dir = test_root / "temp"
    user_data = test_root / "electron-user-data"
    for directory in (local_app_data, roaming_app_data, temp_dir, user_data):
        directory.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    environment.update(
        {
            "LOCALAPPDATA": str(local_app_data),
            "APPDATA": str(roaming_app_data),
            "TEMP": str(temp_dir),
            "TMP": str(temp_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "ELECTRON_DISABLE_CRASH_REPORTING": "1",
            "NOTEBOOK_AI_RUNTIME_ROOT": str(runtime_root),
            "NOTEBOOK_AI_DATA_PROJECT_ROOT": str(data_project_root),
            "NOTEBOOK_AI_PYTHON_EXE": str(python_exe),
            "NOTEBOOK_AI_NODE_EXE": str(node_exe),
            "NOTEBOOK_AI_BACKEND_PORT": str(arguments.backend_port),
            "NOTEBOOK_AI_BACKEND_URL": f"http://127.0.0.1:{arguments.backend_port}",
            "NOTEBOOK_AI_MCP_PORT": str(arguments.mcp_port),
        }
    )
    environment.pop("NOTEBOOK_AI_PROJECT_ROOT", None)
    environment.pop("PYTHONPATH", None)
    environment.pop("NODE_PATH", None)
    launcher = runtime_root / "scripts" / "runtime" / "notebook_ai_launcher.py"
    stdout_path = test_root / "search.stdout.log"
    stderr_path = test_root / "search.stderr.log"
    baseline = snapshot_windows()
    monitor = WindowMonitor(baseline)
    search_process: subprocess.Popen[bytes] | None = None
    runtime_pids: list[int] = []
    started_at = time.time()
    primary_error: BaseException | None = None
    result: dict[str, Any] = {}
    stdout_stream = stdout_path.open("wb")
    stderr_stream = stderr_path.open("wb")
    monitor.start()
    try:
        search_process = subprocess.Popen(
            [
                str(executable),
                f"--user-data-dir={user_data}",
                "--no-first-run",
                "--disable-breakpad",
                "--disable-crash-reporter",
            ],
            cwd=str(executable.parent),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout_stream,
            stderr=stderr_stream,
            shell=False,
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        fastapi_health = wait_json(
            f"http://127.0.0.1:{arguments.backend_port}/api/v1/retrieval/index/status",
            lambda value: value.get("status") == "ready" and value.get("ready") is True,
            timeout_seconds=arguments.timeout,
            process=search_process,
        )
        mcp_health = wait_json(
            f"http://127.0.0.1:{arguments.mcp_port}/healthz",
            lambda value: value.get("status") == "ok"
            and value.get("service") == "notebook-ai-mcp",
            timeout_seconds=arguments.timeout,
            process=search_process,
        )
        status = wait_runtime_owned_ready(
            python_exe,
            launcher,
            runtime_root,
            environment,
            timeout_seconds=15.0,
        )
        for component_name in ("supervisor", "fastapi", "mcp"):
            component = (status.get("components") or {}).get(component_name) or {}
            pid = int(component.get("pid") or 0)
            if pid:
                runtime_pids.append(pid)
        time.sleep(1.0)
        current_windows = snapshot_windows()
        search_windows = [
            window
            for window in current_windows.values()
            if window["pid"] == search_process.pid
            and window["class"] == "Chrome_WidgetWin_1"
            and "Search" in window["title"]
        ]
        if not search_windows:
            raise RuntimeError("headless_cold_start_search_window_missing")
        forbidden = monitor.forbidden_windows()
        if forbidden:
            raise RuntimeError(
                "headless_cold_start_visible_console:" + json.dumps(forbidden, ensure_ascii=False)
            )
        log_file = local_app_data / "NOTEBOOK_AI" / "logs" / "runtime.jsonl"
        if not log_file.is_file() or log_file.stat().st_size == 0:
            raise RuntimeError("headless_cold_start_runtime_log_missing")
        components = status.get("components") or {}
        if not all((components.get(name) or {}).get("owned") is True for name in ("fastapi", "mcp")):
            summary = {
                name: {
                    "owned": (components.get(name) or {}).get("owned"),
                    "state": (components.get(name) or {}).get("state"),
                    "pid": (components.get(name) or {}).get("pid"),
                }
                for name in ("supervisor", "fastapi", "mcp")
            }
            raise RuntimeError(
                "headless_cold_start_owner_not_managed:" + json.dumps(summary, sort_keys=True)
            )
        result = {
            "status": "ready",
            "search_pid": search_process.pid,
            "fastapi": {
                "port": arguments.backend_port,
                "status": fastapi_health.get("status"),
                "pid": (components.get("fastapi") or {}).get("pid"),
                "owner": "managed-by-search",
            },
            "mcp": {
                "port": arguments.mcp_port,
                "status": mcp_health.get("status"),
                "service": mcp_health.get("service"),
                "pid": (components.get("mcp") or {}).get("pid"),
                "owner": "managed-by-search",
            },
            "supervisor_pid": (components.get("supervisor") or {}).get("pid"),
            "visible_search_windows": len(search_windows),
            "new_forbidden_windows": forbidden,
            "window_samples": monitor.sample_count,
            "runtime_log": str(log_file),
            "runtime_log_bytes": log_file.stat().st_size,
            "startup_seconds": round(time.time() - started_at, 3),
        }
    except BaseException as exc:
        primary_error = exc
    finally:
        try:
            run_launcher(python_exe, launcher, runtime_root, environment, "stop")
        except BaseException as exc:
            if primary_error is None:
                primary_error = exc
        if search_process is not None:
            terminate_exact_search_tree(executable, search_process.pid, started_at)
        stdout_stream.close()
        stderr_stream.close()
        monitor.stop()
        residual_ports = [
            port for port in (arguments.backend_port, arguments.mcp_port) if port_is_listening(port)
        ]
        residual_runtime = [pid for pid in runtime_pids if psutil.pid_exists(pid)]
        residual_search = exact_search_pids(executable, started_at)
        if residual_ports or residual_runtime or residual_search:
            cleanup_error = RuntimeError(
                "headless_cold_start_cleanup_failed:"
                + json.dumps(
                    {
                        "ports": residual_ports,
                        "runtime_pids": residual_runtime,
                        "search_pids": residual_search,
                    }
                )
            )
            if primary_error is None:
                primary_error = cleanup_error
        result["cleanup"] = {
            "residual_ports": residual_ports,
            "residual_runtime_pids": residual_runtime,
            "residual_search_pids": residual_search,
        }
    if primary_error is not None:
        raise primary_error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


class WindowMonitor:
    def __init__(self, baseline: dict[int, dict[str, Any]]) -> None:
        self.baseline = baseline
        self._observed: dict[tuple[int, str, str], dict[str, Any]] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="visible-window-monitor", daemon=True)
        self.sample_count = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def forbidden_windows(self) -> list[dict[str, Any]]:
        return sorted(self._observed.values(), key=lambda item: (item["pid"], item["hwnd"]))

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample_count += 1
            for hwnd, window in snapshot_windows().items():
                baseline = self.baseline.get(hwnd)
                unchanged_baseline = baseline == window
                if unchanged_baseline or not is_forbidden_window(window):
                    continue
                key = (hwnd, window["class"], window["title"])
                self._observed[key] = window
            self._stop.wait(0.025)


def snapshot_windows() -> dict[int, dict[str, Any]]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    windows: dict[int, dict[str, Any]] = {}
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(hwnd: int, _parameter: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        class_name = ctypes.create_unicode_buffer(256)
        title = ctypes.create_unicode_buffer(1024)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        user32.GetWindowTextW(hwnd, title, len(title))
        process_name = ""
        executable = ""
        try:
            process = psutil.Process(int(pid.value))
            process_name = process.name()
            executable = process.exe()
        except (psutil.Error, OSError):
            pass
        windows[int(hwnd)] = {
            "hwnd": int(hwnd),
            "pid": int(pid.value),
            "class": class_name.value,
            "title": title.value,
            "process_name": process_name,
            "executable": executable,
        }
        return True

    user32.EnumWindows(collect, 0)
    return windows


def is_forbidden_window(window: dict[str, Any]) -> bool:
    class_name = str(window.get("class") or "").casefold()
    process_name = str(window.get("process_name") or "").casefold()
    return class_name in FORBIDDEN_WINDOW_CLASSES or process_name in FORBIDDEN_PROCESS_NAMES


def run_launcher(
    python_exe: Path,
    launcher: Path,
    runtime_root: Path,
    environment: dict[str, str],
    command: str,
) -> dict[str, Any]:
    startup_info = subprocess.STARTUPINFO()
    startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup_info.wShowWindow = subprocess.SW_HIDE
    completed = subprocess.run(
        [str(python_exe), "-B", str(launcher), command],
        cwd=str(runtime_root),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
        shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
        startupinfo=startup_info,
    )
    lines = [line for line in completed.stdout.decode("utf-8", errors="replace").splitlines() if line]
    if completed.returncode != 0 or not lines:
        raise RuntimeError(f"headless_cold_start_launcher_{command}_failed")
    value = json.loads(lines[-1])
    if value.get("status") == "error":
        raise RuntimeError(f"headless_cold_start_launcher_{command}_failed")
    return value


def wait_runtime_owned_ready(
    python_exe: Path,
    launcher: Path,
    runtime_root: Path,
    environment: dict[str, str],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = run_launcher(python_exe, launcher, runtime_root, environment, "status")
        components = last.get("components") or {}
        if all(
            (components.get(name) or {}).get("owned") is True
            and (components.get(name) or {}).get("state") == "ready"
            and int((components.get(name) or {}).get("pid") or 0) > 0
            for name in ("fastapi", "mcp")
        ):
            return last
        time.sleep(0.25)
    return last


def wait_json(url: str, validator, *, timeout_seconds: float, process) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("headless_cold_start_search_exited")
        try:
            with urlopen(url, timeout=2.0) as response:
                value = json.loads(response.read().decode("utf-8"))
            if validator(value):
                return value
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    raise RuntimeError("headless_cold_start_health_timeout")


def terminate_exact_search_tree(executable: Path, root_pid: int, started_at: float) -> None:
    expected = os.path.normcase(str(executable))
    candidates: list[psutil.Process] = []
    try:
        root = psutil.Process(root_pid)
        candidates.extend(root.children(recursive=True))
        candidates.append(root)
    except psutil.Error:
        pass
    for process in reversed(candidates):
        try:
            if process.create_time() + 1 < started_at:
                continue
            if os.path.normcase(process.exe()) != expected:
                continue
            process.terminate()
        except psutil.Error:
            continue
    _, alive = psutil.wait_procs(candidates, timeout=5.0)
    for process in alive:
        try:
            if os.path.normcase(process.exe()) == expected:
                process.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=5.0)


def exact_search_pids(executable: Path, started_at: float) -> list[int]:
    expected = os.path.normcase(str(executable))
    result: list[int] = []
    for process in psutil.process_iter(["pid", "exe", "create_time"]):
        try:
            if process.info["create_time"] + 1 < started_at:
                continue
            if os.path.normcase(process.info["exe"] or "") == expected:
                result.append(int(process.info["pid"]))
        except (psutil.Error, OSError):
            continue
    return sorted(result)


def ensure_port_available(port: int) -> None:
    if port_is_listening(port):
        raise RuntimeError(f"headless_cold_start_port_in_use:{port}")


def port_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--test-root", type=Path, required=True)
    parser.add_argument("--python-exe", type=Path, required=True)
    parser.add_argument("--node-exe", type=Path, required=True)
    parser.add_argument("--backend-port", type=int, default=18080)
    parser.add_argument("--mcp-port", type=int, default=18787)
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
