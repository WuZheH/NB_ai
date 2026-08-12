"""Process-isolated chaptered PDF import jobs.

The FastAPI process owns only job files and worker process handles. The
expensive classify/parse/apply pipeline runs in scripts/run_chaptered_import_job_worker.py.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.paths import DATA_DIR, DEFAULT_DB_PATH, RUNTIME_PROJECT_ROOT, RUNTIME_STATE_DIR
from app.services.pdf_parser_backends import MARKER_SURYA_PAGE_BLOCKS_BACKEND, probe_runtime
from app.services.production_write_surface_guard import (
    require_proven_legacy_for_legacy_write_surface,
)


STAGES: dict[str, int] = {
    "queued": 0,
    "classifying": 5,
    "parsing_pdf": 15,
    "detecting_chapters": 35,
    "splitting_chunks": 60,
    "writing_db": 80,
    "verifying": 95,
    "completed": 100,
    "failed": 100,
    "cancelled": 100,
}

ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
NON_CANCELABLE_STAGES = frozenset({"writing_db", "verifying"})
HEARTBEAT_STALE_SECONDS = 120
WORKER_LOG_TAIL_LINES = 50

DEFAULT_IMPORT_JOBS_ROOT = RUNTIME_STATE_DIR / "import_jobs"
IMPORT_JOBS_ROOT = DEFAULT_IMPORT_JOBS_ROOT
WORKER_SCRIPT = RUNTIME_PROJECT_ROOT / "scripts" / "run_chaptered_import_job_worker.py"
STATUS_REPLACE_MAX_ATTEMPTS = 10

_lock = threading.Lock()
_processes: dict[str, subprocess.Popen[Any]] = {}


class StatusWriteError(RuntimeError):
    """Raised when a status file cannot be safely written after retries."""


def create_chaptered_import_job_process(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a process-isolated chaptered import job.

    If the same normalized PDF path already has a live queued/running worker,
    the existing job snapshot is returned with duplicate flags.
    """
    require_proven_legacy_for_legacy_write_surface(
        error_code="chaptered_import_job_versioned_frozen",
        message=(
            "后台 chaptered PDF import job 在 versioned production 中已冻结；"
            "本次请求未创建 job artifact，也未启动 worker。"
        ),
        db_path=DEFAULT_DB_PATH,
        data_dir=DATA_DIR,
    )
    object_import_mode = str(payload.get("object_import_mode") or "")
    if object_import_mode != "chaptered":
        raise ValueError("Only chaptered imports are supported via job endpoint.")

    pdf_path = str(payload.get("pdf_path") or "")
    normalized_pdf_path = normalize_pdf_path(pdf_path)
    backend = str(payload.get("backend") or MARKER_SURYA_PAGE_BLOCKS_BACKEND)
    import_granularity = str(payload.get("import_granularity") or "chapter")

    root = _jobs_root()
    root.mkdir(parents=True, exist_ok=True)

    existing = _find_duplicate_running_job(normalized_pdf_path)
    if existing is not None:
        return {
            **existing,
            "reused_existing_job": True,
            "duplicate_running_job": True,
        }

    job_id = f"chaptered-import-{uuid.uuid4().hex[:12]}"
    job_dir = root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    payload_file = job_dir / "payload.json"
    status_file = job_dir / "status.json"
    worker_log = job_dir / "worker.log"

    runtime = probe_runtime(backend=backend)
    device_selection = select_worker_device(payload, runtime=runtime, backend=backend)
    worker_device = device_selection["worker_device"]
    device_selection_reason = device_selection["device_selection_reason"]
    worker_gpu_name = device_selection.get("worker_gpu_name")
    runtime = {
        **runtime,
        "recommended_worker_device": runtime.get("recommended_worker_device") or worker_device,
        "worker_device": worker_device,
        "worker_gpu_name": worker_gpu_name,
        "device_selection_reason": device_selection_reason,
    }
    warnings = device_warnings(
        backend=backend,
        worker_device=worker_device,
        device_selection_reason=device_selection_reason,
    )

    now = utcnow()
    status = _base_status(
        job_id=job_id,
        pdf_path=pdf_path,
        normalized_pdf_path=normalized_pdf_path,
        backend=backend,
        import_granularity=import_granularity,
        runtime=runtime,
        warnings=warnings,
        worker_device=worker_device,
        worker_gpu_name=worker_gpu_name,
        device_selection_reason=device_selection_reason,
        now=now,
    )

    worker_payload = {
        **payload,
        "parser_device": worker_device,
        "worker_device": worker_device,
        "worker_gpu_name": worker_gpu_name,
        "device_selection_reason": device_selection_reason,
    }
    write_json_atomic(payload_file, worker_payload)
    write_status_atomic(status_file, status)

    cmd = [
        sys.executable,
        str(WORKER_SCRIPT),
        "--job-id",
        job_id,
        "--payload-file",
        str(payload_file),
        "--status-file",
        str(status_file),
        "--worker-log",
        str(worker_log),
    ]

    log_handle = worker_log.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(RUNTIME_PROJECT_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception as exc:
        log_handle.close()
        failed = _merge_status(
            status,
            status="failed",
            stage="failed",
            progress_percent=100,
            message=f"failed to start worker: {exc}",
            error=str(exc),
            traceback_tail=None,
            cancel_allowed=False,
        )
        write_status_atomic(status_file, failed)
        return snapshot_status(failed)
    finally:
        if not log_handle.closed:
            log_handle.close()

    with _lock:
        _processes[job_id] = process

    current = read_status_file(status_file)
    pid_update: dict[str, Any] = {"worker_pid": int(process.pid or 0) or None}
    if current.get("status") == "queued" and current.get("stage") == "queued":
        pid_update.update(
            {
                "status": "running",
                "stage": "queued",
                "message": "Job created; worker process started.",
            }
        )
    running = _merge_status(current, **pid_update)
    write_status_atomic(status_file, running)
    return snapshot_status(running)


def get_import_job_status(job_id: str) -> dict[str, Any]:
    status_file = _status_file_for_job(job_id)
    status = read_status_file(status_file)
    status = reconcile_job_status(status_file, status)
    return snapshot_status(status)


def cancel_import_job_process(job_id: str) -> dict[str, Any]:
    status_file = _status_file_for_job(job_id)
    status = read_status_file(status_file)
    status = reconcile_job_status(status_file, status)

    job_status = str(status.get("status") or "")
    stage = str(status.get("stage") or "")
    if job_status in TERMINAL_STATUSES:
        return {
            **snapshot_status(status),
            "cancel_allowed": False,
            "cancel_message": f"Job already in terminal state: {job_status}",
            "worker_alive": bool(status.get("worker_alive", False)),
            "worker_pid": status.get("worker_pid"),
        }

    if stage in NON_CANCELABLE_STAGES:
        return {
            **snapshot_status({**status, "cancel_allowed": False}),
            "cancel_allowed": False,
            "cancel_message": "cannot cancel safely during DB write or verification",
            "worker_alive": bool(status.get("worker_alive", False)),
            "worker_pid": status.get("worker_pid"),
        }

    requested = _merge_status(
        status,
        cancel_requested=True,
        message="cancelled by user",
        error="cancelled by user",
    )
    write_status_atomic(status_file, requested)

    pid = _int_or_none(requested.get("worker_pid"))
    try:
        _terminate_worker(job_id, pid)
    finally:
        with _lock:
            _processes.pop(job_id, None)

    cancelled = _merge_status(
        requested,
        status="cancelled",
        stage="cancelled",
        progress_percent=100,
        message="cancelled by user",
        error="cancelled by user",
        cancel_requested=True,
        cancel_allowed=True,
    )
    write_status_atomic(status_file, cancelled)
    return {
        **snapshot_status(cancelled),
        "cancel_allowed": True,
        "cancel_message": "cancelled by user",
        "worker_alive": False,
        "worker_pid": cancelled.get("worker_pid"),
    }


def normalize_pdf_path(pdf_path: str) -> str:
    return os.path.normpath(os.path.normcase(str(pdf_path or "").strip()))


def write_json_atomic(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp_path(target)
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, target)
    except Exception as exc:
        if isinstance(exc, StatusWriteError):
            raise
        raise StatusWriteError(f"status write failed: target={target} tmp={tmp}: {exc!r}") from exc
    finally:
        _cleanup_tmp_file(tmp)


def write_status_atomic(status_file: str | Path, status: dict[str, Any]) -> None:
    target = Path(status_file)
    if _would_overwrite_terminal_with_active(target, status):
        return
    write_json_atomic(target, status)


def read_status_file(status_file: str | Path) -> dict[str, Any]:
    path = Path(status_file)
    if not path.exists():
        raise ValueError(f"Import job not found: {path.parent.name}")
    last_error: json.JSONDecodeError | None = None
    for attempt in range(3):
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            break
        except json.JSONDecodeError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))
                continue
            raise ValueError(f"Import job status is unreadable: {path.parent.name}") from exc
    else:
        raise ValueError(f"Import job status is unreadable: {path.parent.name}") from last_error
    if not isinstance(payload, dict):
        raise ValueError(f"Import job status is invalid: {path.parent.name}")
    return payload


def snapshot_status(status: dict[str, Any]) -> dict[str, Any]:
    """Return a response-compatible job snapshot."""
    backend = status.get("backend") or status.get("parser_backend")
    worker_backend = status.get("worker_backend") or backend
    worker_device = status.get("worker_device") or status.get("parser_device") or "unknown"
    runtime = status.get("runtime") or {}
    worker_gpu_name = (status.get("worker_gpu_name") or runtime.get("cuda_device_name")) if worker_device == "cuda" else None
    device_selection_reason = status.get("device_selection_reason") or runtime.get("device_selection_reason") or runtime.get("reason")
    now = utcnow()
    elapsed = elapsed_seconds(status.get("created_at"), now)
    stale_seconds = seconds_since(status.get("heartbeat_at"), now)
    warnings = list(status.get("warnings") or [])
    warnings = _filter_stale_cuda_warnings(warnings, worker_device=worker_device)
    if status.get("heartbeat_stale") and not any("worker heartbeat has not updated recently" in w for w in warnings):
        warnings.append("worker heartbeat has not updated recently; import may be blocked")
    return {
        "job_id": status.get("job_id"),
        "status": status.get("status"),
        "stage": status.get("stage"),
        "progress_percent": status.get("progress_percent", 0),
        "message": status.get("message") or "",
        "pdf_path": status.get("pdf_path") or "",
        "normalized_pdf_path": status.get("normalized_pdf_path") or "",
        "backend": backend,
        "parser_backend": backend,
        "preview_backend": status.get("preview_backend") or "text_layer_cpu",
        "preview_device": status.get("preview_device") or "cpu",
        "worker_backend": worker_backend,
        "worker_device": worker_device,
        "worker_gpu_name": worker_gpu_name,
        "device_selection_reason": device_selection_reason,
        "device_blocker": status.get("device_blocker") or _device_blocker(worker_device, device_selection_reason),
        "import_backend_device": worker_device,
        "import_granularity": status.get("import_granularity"),
        "parser_device": status.get("parser_device") or worker_device,
        "runtime": runtime,
        "warnings": warnings,
        "book_safety_decision": status.get("book_safety_decision") or (status.get("result") or {}).get("book_safety_decision"),
        "book_safety_blockers": status.get("book_safety_blockers") or (status.get("result") or {}).get("book_safety_blockers") or [],
        "book_safety_warnings": status.get("book_safety_warnings") or (status.get("result") or {}).get("book_safety_warnings") or [],
        "detected_chapter_count": status.get("detected_chapter_count") or (status.get("result") or {}).get("detected_chapter_count"),
        "chapter_title_quality": status.get("chapter_title_quality") or (status.get("result") or {}).get("chapter_title_quality"),
        "document_id": status.get("document_id"),
        "result": status.get("result"),
        "error": status.get("error"),
        "traceback_tail": status.get("traceback_tail"),
        "worker_log_tail": status.get("worker_log_tail"),
        "worker_pid": status.get("worker_pid"),
        "worker_alive": status.get("worker_alive"),
        "worker_exit_detected": bool(status.get("worker_exit_detected", False)),
        "created_at": status.get("created_at"),
        "started_at": status.get("started_at"),
        "updated_at": status.get("updated_at"),
        "status_timestamp": status.get("status_timestamp") or status.get("updated_at"),
        "heartbeat_at": status.get("heartbeat_at"),
        "elapsed_seconds": elapsed,
        "stale_seconds": stale_seconds,
        "heartbeat_stale": bool(status.get("heartbeat_stale", False)),
        "current_unit_index": status.get("current_unit_index"),
        "total_units": status.get("total_units"),
        "current_unit_title": status.get("current_unit_title"),
        "current_page_start": status.get("current_page_start"),
        "current_page_end": status.get("current_page_end"),
        "cancel_requested": bool(status.get("cancel_requested", False)),
        "cancel_allowed": bool(status.get("cancel_allowed", True)),
    }


def select_worker_device(payload: dict[str, Any], *, runtime: dict[str, Any], backend: str) -> dict[str, Any]:
    requested = _requested_worker_device(payload)
    marker_backend = backend != "pymupdf_text"
    marker_ready = bool(runtime.get("marker_importable")) and bool(runtime.get("surya_importable"))
    cuda_available = bool(runtime.get("torch_cuda_available", runtime.get("cuda_available")))
    gpu_name = runtime.get("cuda_device_name") if cuda_available else None

    if requested == "cpu":
        return {
            "worker_device": "cpu",
            "worker_gpu_name": None,
            "device_selection_reason": "user_forced_cpu",
        }

    if marker_backend and not marker_ready:
        return {
            "worker_device": "cpu",
            "worker_gpu_name": None,
            "device_selection_reason": "marker_cuda_unavailable",
        }

    if requested == "cuda":
        if cuda_available:
            return {
                "worker_device": "cuda",
                "worker_gpu_name": gpu_name,
                "device_selection_reason": "cuda_available",
            }
        return {
            "worker_device": "cpu",
            "worker_gpu_name": None,
            "device_selection_reason": _runtime_cpu_reason(runtime),
        }

    if cuda_available:
        return {
            "worker_device": "cuda",
            "worker_gpu_name": gpu_name,
            "device_selection_reason": "cuda_available",
        }
    return {
        "worker_device": "cpu",
        "worker_gpu_name": None,
        "device_selection_reason": _runtime_cpu_reason(runtime),
    }


def device_warnings(*, backend: str, worker_device: str, device_selection_reason: str | None) -> list[str]:
    if backend == "pymupdf_text" or worker_device == "cuda":
        return []
    reason = device_selection_reason or "torch_cuda_unavailable"
    return [f"current backend is not using CUDA; long PDF import may be slow: {reason}"]


def _requested_worker_device(payload: dict[str, Any]) -> str:
    for key in ("worker_device", "parser_device", "device"):
        value = str(payload.get(key) or "").strip().lower()
        if value in {"cpu", "cuda"}:
            return value
        if value in {"gpu", "auto"}:
            return "cuda" if value == "gpu" else "auto"
    return "auto"


def _runtime_cpu_reason(runtime: dict[str, Any]) -> str:
    reason = str(runtime.get("reason") or "").strip()
    if reason in {"torch_cuda_unavailable", "marker_cuda_unavailable", "probe_failed", "cuda_probe_failed"}:
        return "cuda_probe_failed" if reason == "probe_failed" else reason
    if runtime.get("torch_probe_error"):
        return "cuda_probe_failed"
    return "torch_cuda_unavailable"


def _device_blocker(worker_device: str, reason: str | None) -> str | None:
    if worker_device != "cpu":
        return None
    if reason == "user_forced_cpu":
        return None
    return reason or "torch_cuda_unavailable"


def _filter_stale_cuda_warnings(warnings: list[str], *, worker_device: str) -> list[str]:
    if str(worker_device).lower() != "cuda":
        return warnings
    return [warning for warning in warnings if "current backend is not using CUDA" not in str(warning)]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jobs_root() -> Path:
    return Path(IMPORT_JOBS_ROOT)


def _job_dir(job_id: str) -> Path:
    root = _jobs_root()
    path = root / job_id
    if not path.is_dir():
        raise ValueError(f"Import job not found: {job_id}")
    return path


def _status_file_for_job(job_id: str) -> Path:
    return _job_dir(job_id) / "status.json"


def _iter_status_files() -> list[Path]:
    root = _jobs_root()
    if not root.exists():
        return []
    return sorted(root.glob("chaptered-import-*/status.json"))


def _find_duplicate_running_job(normalized_pdf_path: str) -> dict[str, Any] | None:
    for status_file in _iter_status_files():
        try:
            status = read_status_file(status_file)
        except ValueError:
            continue
        if status.get("normalized_pdf_path") != normalized_pdf_path:
            continue
        status = reconcile_job_status(status_file, status)
        if status.get("status") in ACTIVE_STATUSES and status.get("worker_alive") is True:
            return snapshot_status(status)
    return None


def reconcile_job_status(status_file: str | Path, status: dict[str, Any] | None = None) -> dict[str, Any]:
    path = Path(status_file)
    current = dict(status or read_status_file(path))
    now = utcnow()
    current["elapsed_seconds"] = elapsed_seconds(current.get("created_at"), now)
    current["stale_seconds"] = seconds_since(current.get("heartbeat_at"), now)

    if current.get("status") not in ACTIVE_STATUSES:
        return _with_worker_log_tail(path, current)

    pid = _int_or_none(current.get("worker_pid"))
    alive = _is_worker_alive(str(current.get("job_id") or path.parent.name), pid) if pid else False
    current["worker_alive"] = alive

    if pid and not alive:
        failed = _merge_status(
            _with_worker_log_tail(path, current),
            status="failed",
            stage="failed",
            progress_percent=100,
            message="worker process exited unexpectedly",
            error="worker process exited unexpectedly",
            cancel_allowed=False,
            worker_exit_detected=True,
            worker_alive=False,
        )
        write_status_atomic(path, failed)
        return failed

    heartbeat_stale = bool(
        alive
        and current.get("status") in ACTIVE_STATUSES
        and int(current.get("stale_seconds") or 0) > HEARTBEAT_STALE_SECONDS
    )
    current["heartbeat_stale"] = heartbeat_stale
    if heartbeat_stale:
        current["cancel_allowed"] = str(current.get("stage") or "") not in NON_CANCELABLE_STAGES
    return _with_worker_log_tail(path, current)


def _mark_failed_if_worker_exited(status_file: Path, status: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for older tests."""
    return reconcile_job_status(status_file, status)


def _legacy_mark_failed_if_worker_exited(status_file: Path, status: dict[str, Any]) -> dict[str, Any]:
    if status.get("status") not in ACTIVE_STATUSES:
        return status
    pid = _int_or_none(status.get("worker_pid"))
    if not pid:
        return status
    job_id = str(status.get("job_id") or status_file.parent.name)
    if _is_worker_alive(job_id, pid):
        return status
    failed = _merge_status(
        status,
        status="failed",
        stage="failed",
        progress_percent=100,
        message="worker process exited before job reached a terminal state",
        error="worker process exited before job reached a terminal state",
        cancel_allowed=False,
    )
    write_status_atomic(status_file, failed)
    return failed


def _is_worker_alive(job_id: str, pid: int) -> bool:
    with _lock:
        process = _processes.get(job_id)
    if process is not None:
        return process.poll() is None
    return worker_alive(pid)


def worker_alive(pid: int | None) -> bool:
    parsed_pid = _int_or_none(pid)
    if not parsed_pid:
        return False
    if os.name == "nt":
        alive = _worker_alive_tasklist(parsed_pid)
        if alive is not None:
            return alive
    try:
        os.kill(parsed_pid, 0)
    except (OSError, PermissionError, ProcessLookupError, ValueError):
        return False
    return True


def is_pid_running(pid: int | None) -> bool:
    return worker_alive(pid)


def _worker_alive_tasklist(pid: int) -> bool | None:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    output = (result.stdout or "").strip()
    if not output or output.upper().startswith("INFO:"):
        return False
    for line in output.splitlines():
        fields = [part.strip().strip('"') for part in line.split(",")]
        if len(fields) >= 2 and fields[1] == str(pid):
            return True
    return False


def _terminate_worker(job_id: str, pid: int | None) -> None:
    with _lock:
        process = _processes.get(job_id)
    if process is not None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        return
    if pid:
        _terminate_pid(pid)


def _terminate_pid(pid: int) -> None:
    if not worker_alive(pid):
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not worker_alive(pid):
            return
        time.sleep(0.05)
    if os.name != "nt":
        os.kill(pid, signal.SIGKILL)
    else:
        os.kill(pid, signal.SIGTERM)


def _base_status(
    *,
    job_id: str,
    pdf_path: str,
    normalized_pdf_path: str,
    backend: str,
    import_granularity: str,
    runtime: dict[str, Any],
    warnings: list[str],
    now: str,
    worker_device: str | None = None,
    worker_gpu_name: str | None = None,
    device_selection_reason: str | None = None,
) -> dict[str, Any]:
    parser_device = str(worker_device or runtime.get("recommended_worker_device") or runtime.get("expected_device") or "unknown")
    reason = device_selection_reason or runtime.get("device_selection_reason") or runtime.get("reason")
    gpu_name = worker_gpu_name or (runtime.get("cuda_device_name") if parser_device == "cuda" else None)
    return {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "progress_percent": 0,
        "message": "Job created, waiting to start.",
        "pdf_path": pdf_path,
        "normalized_pdf_path": normalized_pdf_path,
        "backend": backend,
        "parser_backend": backend,
        "preview_backend": "text_layer_cpu",
        "preview_device": "cpu",
        "worker_backend": backend,
        "worker_device": parser_device,
        "worker_gpu_name": gpu_name,
        "device_selection_reason": reason,
        "device_blocker": _device_blocker(parser_device, str(reason or "")),
        "import_backend_device": parser_device,
        "import_granularity": import_granularity,
        "parser_device": parser_device,
        "runtime": runtime,
        "warnings": warnings,
        "document_id": None,
        "result": None,
        "error": None,
        "traceback_tail": None,
        "worker_log_tail": None,
        "worker_pid": None,
        "worker_alive": None,
        "worker_exit_detected": False,
        "started_at": None,
        "current_unit_index": None,
        "total_units": None,
        "current_unit_title": None,
        "current_page_start": None,
        "current_page_end": None,
        "created_at": now,
        "updated_at": now,
        "status_timestamp": now,
        "heartbeat_at": now,
        "elapsed_seconds": 0,
        "cancel_requested": False,
        "cancel_allowed": True,
    }


def _merge_status(current: dict[str, Any], **updates: Any) -> dict[str, Any]:
    now = utcnow()
    merged = dict(current)
    merged.update(updates)
    merged["updated_at"] = now
    merged["status_timestamp"] = now
    merged["heartbeat_at"] = now
    merged["elapsed_seconds"] = elapsed_seconds(merged.get("created_at"), now)
    return merged


def _unique_tmp_path(target: Path) -> Path:
    return target.with_name(
        f"{target.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:12]}.tmp"
    )


def _replace_with_retry(tmp: Path, target: Path, *, max_attempts: int = STATUS_REPLACE_MAX_ATTEMPTS) -> None:
    delay = 0.05
    last_error: BaseException | None = None
    for attempt in range(max_attempts):
        if not tmp.exists():
            raise StatusWriteError(f"status write failed: temp file disappeared before replace: target={target} tmp={tmp}")
        try:
            os.replace(tmp, target)
            return
        except (PermissionError, OSError) as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                break
            time.sleep(delay)
            delay = min(delay * 2, 1.0)
    raise StatusWriteError(
        f"status write failed after {max_attempts} attempts: target={target} tmp={tmp}: {last_error!r}"
    )


def _cleanup_tmp_file(tmp: Path) -> None:
    try:
        if tmp.exists():
            tmp.unlink()
    except OSError:
        pass


def _would_overwrite_terminal_with_active(target: Path, new_status: dict[str, Any]) -> bool:
    new_value = str(new_status.get("status") or "")
    if new_value not in ACTIVE_STATUSES:
        return False
    if not target.exists():
        return False
    try:
        existing = read_status_file(target)
    except ValueError:
        return False
    return str(existing.get("status") or "") in TERMINAL_STATUSES


def elapsed_seconds(created_at: Any, now: str | None = None) -> int:
    if not created_at:
        return 0
    try:
        created = datetime.fromisoformat(str(created_at))
        current = datetime.fromisoformat(now or utcnow())
        return max(0, int((current - created).total_seconds()))
    except Exception:
        return 0


def seconds_since(timestamp: Any, now: str | None = None) -> int:
    if not timestamp:
        return 0
    try:
        then = datetime.fromisoformat(str(timestamp))
        current = datetime.fromisoformat(now or utcnow())
        return max(0, int((current - then).total_seconds()))
    except Exception:
        return 0


def read_text_tail(path: str | Path, line_count: int = WORKER_LOG_TAIL_LINES) -> str | None:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return None
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return "\n".join(lines[-line_count:]) if lines else None


def _with_worker_log_tail(status_file: Path, status: dict[str, Any]) -> dict[str, Any]:
    tail = read_text_tail(status_file.parent / "worker.log")
    if not tail:
        return status
    merged = dict(status)
    merged["worker_log_tail"] = tail
    if not merged.get("traceback_tail") and merged.get("status") == "failed":
        merged["traceback_tail"] = tail
    return merged


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
