from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from app.services.retrieval.fts_schema import ORDINARY_TABLE


def apply_duplicate_policy(
    connection: sqlite3.Connection,
    candidates: list[dict[str, Any]],
    *,
    collapse_duplicates: bool,
) -> tuple[list[dict[str, Any]], int]:
    group_ids = sorted(
        {
            str(item["duplicate_group_id"])
            for item in candidates
            if item.get("duplicate_candidate") and item.get("duplicate_group_id")
        }
    )
    members = _load_members(connection, group_ids)
    enriched = [_with_duplicate_metadata(item, members) for item in candidates]
    if not collapse_duplicates:
        return enriched, 0

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in enriched:
        key = (
            f"group:{item['duplicate_group_id']}"
            if item.get("duplicate_candidate") and item.get("duplicate_group_id")
            else f"fragment:{item['fragment_id']}"
        )
        grouped[key].append(item)

    collapsed: list[dict[str, Any]] = []
    removed = 0
    for group in grouped.values():
        representative = max(
            group,
            key=lambda item: (
                float(item.get("score") or 0.0),
                float(item.get("base_bm25_score") or 0.0),
                -int(item.get("base_bm25_rank") or 2**31),
            ),
        )
        if len(group) > 1:
            removed += len(group) - 1
            representative = {
                **representative,
                "retrieval_channels": sorted(
                    {
                        channel
                        for item in group
                        for channel in item.get("retrieval_channels", [])
                    }
                ),
                "match_reasons": list(
                    dict.fromkeys(
                        reason
                        for item in group
                        for reason in item.get("match_reasons", [])
                    )
                ),
            }
        collapsed.append(representative)
    return collapsed, removed


def _load_members(
    connection: sqlite3.Connection,
    group_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    if not group_ids:
        return {}
    placeholders = ",".join("?" for _ in group_ids)
    rows = connection.execute(
        f"""
        SELECT
            duplicate_group_id,
            fragment_id,
            source_type,
            provenance_json,
            warnings_json
        FROM {ORDINARY_TABLE}
        WHERE duplicate_candidate = 1
          AND duplicate_group_id IN ({placeholders})
        ORDER BY duplicate_group_id, fragment_id
        """,
        group_ids,
    ).fetchall()
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[str(row["duplicate_group_id"])].append(
            {
                "fragment_id": str(row["fragment_id"]),
                "source_type": str(row["source_type"]),
                "provenance": _json_list(row["provenance_json"]),
                "warnings": _json_list(row["warnings_json"]),
            }
        )
    return result


def _with_duplicate_metadata(
    candidate: dict[str, Any],
    members: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    group_id = candidate.get("duplicate_group_id")
    group = (
        members.get(str(group_id), [])
        if candidate.get("duplicate_candidate") and group_id
        else []
    )
    if not group:
        return {
            **candidate,
            "duplicate_count": 1,
            "duplicate_fragment_ids": [str(candidate["fragment_id"])],
            "duplicate_source_types": [str(candidate["source_type"])],
        }

    provenance = []
    warnings = list(candidate.get("warnings") or [])
    for member in group:
        for entry in member["provenance"]:
            provenance.append(
                {
                    **entry,
                    "duplicate_fragment_id": member["fragment_id"],
                    "duplicate_source_type": member["source_type"],
                }
            )
        warnings.extend(str(value) for value in member["warnings"])
    return {
        **candidate,
        "duplicate_count": len(group),
        "duplicate_fragment_ids": [item["fragment_id"] for item in group],
        "duplicate_source_types": sorted({item["source_type"] for item in group}),
        "provenance": provenance,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _json_list(value: str) -> list[Any]:
    try:
        payload = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []
