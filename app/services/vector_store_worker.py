from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any

from app.services import vector_store_service


VECTOR_STORE_WORKER_ENABLED = True
VECTOR_STORE_AUTO_SYNC_ENABLED = False
VECTOR_STORE_AUTO_SYNC_DEFAULT = "disabled"
VECTOR_STORE_STARTUP_DELAY_SECONDS = 30
VECTOR_STORE_SYNC_INTERVAL_SECONDS = 21_600
VECTOR_STORE_AUTO_SYNC_THRESHOLD = 20
VECTOR_STORE_AUTO_SYNC_KIND = "all"
VECTOR_STORE_AUTO_DELETE_ORPHANS = False

logger = logging.getLogger(__name__)

_TASK: asyncio.Task[None] | None = None
_STATE: dict[str, Any] = {
    "enabled": VECTOR_STORE_WORKER_ENABLED,
    "auto_sync_enabled": VECTOR_STORE_AUTO_SYNC_ENABLED,
    "auto_sync_default": VECTOR_STORE_AUTO_SYNC_DEFAULT,
    "vector_store_write_performed": False,
    "interval_seconds": VECTOR_STORE_SYNC_INTERVAL_SECONDS,
    "startup_delay_seconds": VECTOR_STORE_STARTUP_DELAY_SECONDS,
    "auto_sync_threshold": VECTOR_STORE_AUTO_SYNC_THRESHOLD,
    "auto_sync_kind": VECTOR_STORE_AUTO_SYNC_KIND,
    "auto_delete_orphans": VECTOR_STORE_AUTO_DELETE_ORPHANS,
    "running": False,
    "last_check_at": None,
    "last_sync_at": None,
    "last_result": None,
    "last_error": None,
}


def start_vector_store_worker(
    *,
    enabled: bool = VECTOR_STORE_WORKER_ENABLED,
    auto_sync_enabled: bool = VECTOR_STORE_AUTO_SYNC_ENABLED,
    startup_delay_seconds: int = VECTOR_STORE_STARTUP_DELAY_SECONDS,
    interval_seconds: int = VECTOR_STORE_SYNC_INTERVAL_SECONDS,
    auto_sync_threshold: int = VECTOR_STORE_AUTO_SYNC_THRESHOLD,
    auto_sync_kind: str = VECTOR_STORE_AUTO_SYNC_KIND,
    auto_delete_orphans: bool = VECTOR_STORE_AUTO_DELETE_ORPHANS,
) -> None:
    global _TASK
    _STATE.update(
        {
            "enabled": bool(enabled),
            "auto_sync_enabled": bool(auto_sync_enabled),
            "auto_sync_default": VECTOR_STORE_AUTO_SYNC_DEFAULT,
            "vector_store_write_performed": False,
            "startup_delay_seconds": int(startup_delay_seconds),
            "interval_seconds": int(interval_seconds),
            "auto_sync_threshold": int(auto_sync_threshold),
            "auto_sync_kind": auto_sync_kind,
            "auto_delete_orphans": bool(auto_delete_orphans),
        }
    )
    if not enabled:
        logger.info("vector_store_worker_disabled")
        return
    if _TASK is not None and not _TASK.done():
        logger.info("vector_store_worker_already_running")
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("vector_store_worker_no_running_loop")
        return
    _TASK = loop.create_task(
        vector_store_worker_loop(
            startup_delay_seconds=startup_delay_seconds,
            interval_seconds=interval_seconds,
            auto_sync_enabled=auto_sync_enabled,
            threshold=auto_sync_threshold,
            kind=auto_sync_kind,
            delete_orphans=auto_delete_orphans,
        )
    )
    _STATE["running"] = True
    logger.info("vector_store_worker_started")


async def stop_vector_store_worker() -> None:
    global _TASK
    task = _TASK
    _TASK = None
    _STATE["running"] = False
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        logger.info("vector_store_worker_cancelled")


async def vector_store_worker_loop(
    *,
    startup_delay_seconds: int = VECTOR_STORE_STARTUP_DELAY_SECONDS,
    interval_seconds: int = VECTOR_STORE_SYNC_INTERVAL_SECONDS,
    auto_sync_enabled: bool = VECTOR_STORE_AUTO_SYNC_ENABLED,
    threshold: int = VECTOR_STORE_AUTO_SYNC_THRESHOLD,
    kind: str = VECTOR_STORE_AUTO_SYNC_KIND,
    delete_orphans: bool = VECTOR_STORE_AUTO_DELETE_ORPHANS,
) -> None:
    try:
        await asyncio.sleep(startup_delay_seconds)
        while True:
            await run_vector_store_sync_check_once(
                auto_sync_enabled=auto_sync_enabled,
                threshold=threshold,
                kind=kind,
                delete_orphans=delete_orphans,
            )
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - defensive guard for app uptime
        _record_error(exc)
        logger.exception("vector_store_worker_loop_failed")
    finally:
        _STATE["running"] = False


async def run_vector_store_sync_check_once(
    *,
    auto_sync_enabled: bool = VECTOR_STORE_AUTO_SYNC_ENABLED,
    threshold: int = VECTOR_STORE_AUTO_SYNC_THRESHOLD,
    kind: str = VECTOR_STORE_AUTO_SYNC_KIND,
    delete_orphans: bool = VECTOR_STORE_AUTO_DELETE_ORPHANS,
) -> dict[str, Any]:
    checked_at = _utc_now()
    _STATE["last_check_at"] = checked_at
    _STATE["last_error"] = None
    _STATE["auto_sync_enabled"] = bool(auto_sync_enabled)
    _STATE["vector_store_write_performed"] = False
    try:
        status = await asyncio.to_thread(vector_store_service.check_vector_store_status)
        sync_stats = status.get("sync") or {}
        changed_count = _missing_stale_count(sync_stats)
        result: dict[str, Any] = {
            "checked_at": checked_at,
            "available": bool(status.get("available")),
            "stale": bool(status.get("stale")),
            "reason": status.get("reason"),
            "sync_summary": format_sync_summary(sync_stats),
            "auto_sync_performed": False,
            "auto_sync_enabled": bool(auto_sync_enabled),
            "auto_sync_default": VECTOR_STORE_AUTO_SYNC_DEFAULT,
            "vector_store_write_performed": False,
            "auto_sync_skipped_reason": None,
            "sync_results": [],
        }

        if not status.get("available"):
            result["auto_sync_skipped_reason"] = "vector_store_unavailable"
            _STATE["last_result"] = result
            logger.warning("vector_store_unavailable_for_worker_sync: %s", result)
            return result

        if not auto_sync_enabled:
            result["auto_sync_skipped_reason"] = (
                "vector_auto_sync_disabled"
                if changed_count
                else "vector_store_status_checked_auto_sync_disabled"
            )
        elif should_auto_sync(sync_stats, threshold, auto_sync_enabled=auto_sync_enabled):
            sync_results = await asyncio.to_thread(
                vector_store_service.sync_vector_store,
                kind,
                dry_run=False,
                delete_orphans=delete_orphans,
            )
            result["auto_sync_performed"] = True
            result["vector_store_write_performed"] = True
            result["sync_results"] = sync_results
            _STATE["last_sync_at"] = _utc_now()
            _STATE["vector_store_write_performed"] = True
            logger.info("vector_store_auto_sync_completed: %s", result)
        else:
            result["auto_sync_skipped_reason"] = (
                "vector_store_needs_manual_sync"
                if changed_count > threshold
                else "vector_store_already_current"
            )
            if changed_count > threshold:
                logger.warning("vector_store_needs_manual_sync: %s", result)
            else:
                logger.info("vector_store_already_current: %s", result)

        _STATE["last_result"] = result
        return result
    except Exception as exc:
        _record_error(exc)
        logger.exception("vector_store_worker_check_failed")
        return {
            "checked_at": checked_at,
            "available": False,
            "stale": False,
            "reason": "vector_store_worker_error",
            "auto_sync_performed": False,
            "auto_sync_enabled": bool(auto_sync_enabled),
            "auto_sync_default": VECTOR_STORE_AUTO_SYNC_DEFAULT,
            "vector_store_write_performed": False,
            "auto_sync_skipped_reason": "vector_store_worker_error",
            "error": str(exc),
        }


def should_auto_sync(
    sync_stats: dict[str, Any],
    threshold: int = VECTOR_STORE_AUTO_SYNC_THRESHOLD,
    *,
    auto_sync_enabled: bool = VECTOR_STORE_AUTO_SYNC_ENABLED,
) -> bool:
    if not auto_sync_enabled:
        return False
    changed_count = _missing_stale_count(sync_stats)
    return 0 < changed_count <= threshold


def format_sync_summary(sync_stats: dict[str, Any]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for kind in ("passages", "objects"):
        stats = sync_stats.get(kind) or {}
        summary[kind] = {
            "source_count": int(stats.get("source_count") or 0),
            "indexed_count": int(stats.get("indexed_count") or 0),
            "missing_count": int(stats.get("missing_count") or 0),
            "stale_count": int(stats.get("stale_count") or 0),
            "orphan_count": int(stats.get("orphan_count") or 0),
        }
    return summary


def worker_status() -> dict[str, Any]:
    state = dict(_STATE)
    state["running"] = _TASK is not None and not _TASK.done()
    return state


def vector_auto_sync_boundary() -> dict[str, Any]:
    return {
        "vector_auto_sync_enabled": bool(_STATE.get("auto_sync_enabled")),
        "vector_auto_sync_default": VECTOR_STORE_AUTO_SYNC_DEFAULT,
        "vector_store_write_performed": bool(_STATE.get("vector_store_write_performed")),
    }


def _missing_stale_count(sync_stats: dict[str, Any]) -> int:
    total = 0
    for stats in (sync_stats.get("passages") or {}, sync_stats.get("objects") or {}):
        total += int(stats.get("missing_count") or 0)
        total += int(stats.get("stale_count") or 0)
    return total


def _record_error(exc: Exception) -> None:
    _STATE["last_error"] = str(exc)
    _STATE["last_result"] = {
        "auto_sync_performed": False,
        "auto_sync_skipped_reason": "vector_store_worker_error",
        "error": str(exc),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
