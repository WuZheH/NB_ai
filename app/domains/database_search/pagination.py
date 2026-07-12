"""Stable ordering and result de-duplication helpers."""

from __future__ import annotations

from typing import Any


def _page_sort(row: dict[str, Any]) -> int:
    for key in ("pdf_page_start", "pdf_page", "page"):
        if row.get(key) is not None:
            return int(row.get(key) or 0)
    return 0


def _dedupe_results(
    results: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in results:
        value = str(item.get(key) or "")
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(item)
    return deduped


__all__ = ["_dedupe_results", "_page_sort"]
