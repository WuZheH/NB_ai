from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


MODE = "phase110k_q4_mechanism_draft_review_cli_v1"
DEFAULT_DB_PATH = Path("data/db/research_memory.db")
REVIEWABLE_STATUSES = {"pending", "needs_edit", "deferred"}
TERMINAL_STATUSES = {"accepted", "rejected", "merged"}
ACTION_TO_STATUS = {
    "accept": "accepted",
    "reject": "rejected",
    "needs_edit": "needs_edit",
    "defer": "deferred",
    "merge_into": "merged",
}
ACTION_TO_DECISION = {
    "accept": "accepted",
    "reject": "rejected",
    "needs_edit": "needs_edit",
    "defer": "deferred",
    "merge_into": "merged",
}


def build_mechanism_draft_review_report(
    db_path: str | Path,
    *,
    draft_id: str,
    action: str,
    review_notes: str | None = None,
    merge_into_draft_id: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    if action not in ACTION_TO_STATUS:
        blockers.append("invalid_review_action")

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return _report(
            status="FAIL",
            draft_id=draft_id,
            current_status=None,
            requested_action=action,
            proposed_status=None,
            proposed_decision=None,
            dry_run=not apply,
            apply=apply,
            blockers=[f"db_open_failed:{exc}"],
            warnings=warnings,
        )

    affected_rows = 0
    row: sqlite3.Row | None = None
    target_row: sqlite3.Row | None = None
    try:
        row = _fetch_draft(conn, draft_id)
        if row is None:
            blockers.append("mechanism_draft_candidate_not_found")
        current_status = str(row["review_status"]) if row is not None else None
        proposed_status = ACTION_TO_STATUS.get(action)
        proposed_decision = ACTION_TO_DECISION.get(action)

        if row is not None:
            if current_status in TERMINAL_STATUSES:
                blockers.append("terminal_review_status_cannot_transition")
            elif current_status not in REVIEWABLE_STATUSES:
                blockers.append("current_review_status_not_reviewable")

        if action == "merge_into":
            if not merge_into_draft_id:
                blockers.append("merge_into_requires_target_draft_id")
            elif merge_into_draft_id == draft_id:
                blockers.append("merge_into_target_must_be_different")
            else:
                target_row = _fetch_draft(conn, merge_into_draft_id)
                if target_row is None:
                    blockers.append("merge_into_target_not_found")

        if review_notes is None or not review_notes.strip():
            warnings.append("review_notes_recommended")

        if blockers or not apply:
            return _report(
                status="BLOCKED" if blockers else "OK",
                draft_id=draft_id,
                current_status=current_status,
                requested_action=action,
                proposed_status=proposed_status,
                proposed_decision=proposed_decision,
                dry_run=not apply,
                apply=apply,
                blockers=list(dict.fromkeys(blockers)),
                warnings=list(dict.fromkeys(warnings)),
                review_notes=review_notes,
                merge_into_draft_id=merge_into_draft_id,
                target_draft_id=target_row["draft_id"] if target_row is not None else None,
            )

        reviewed_at = _utc_now()
        with conn:
            cursor = conn.execute(
                """
                UPDATE mechanism_draft_candidates
                SET review_status = ?,
                    review_decision = ?,
                    review_notes = ?,
                    merged_into_draft_id = ?,
                    reviewed_at = ?,
                    updated_at = ?
                WHERE draft_id = ?
                """,
                (
                    proposed_status,
                    proposed_decision,
                    review_notes,
                    merge_into_draft_id if action == "merge_into" else None,
                    reviewed_at,
                    reviewed_at,
                    draft_id,
                ),
            )
            affected_rows = cursor.rowcount
            if affected_rows != 1:
                raise sqlite3.DatabaseError(
                    f"review_update_affected_rows_must_equal_1:{affected_rows}"
                )
        updated = _fetch_draft(conn, draft_id)
        return _report(
            status="OK",
            draft_id=draft_id,
            current_status=current_status,
            requested_action=action,
            proposed_status=proposed_status,
            proposed_decision=proposed_decision,
            dry_run=False,
            apply=True,
            blockers=[],
            warnings=list(dict.fromkeys(warnings)),
            review_notes=review_notes,
            merge_into_draft_id=merge_into_draft_id,
            affected_rows=affected_rows,
            reviewed_at=updated["reviewed_at"] if updated is not None else reviewed_at,
            db_write_performed=True,
        )
    except sqlite3.Error as exc:
        return _report(
            status="FAIL",
            draft_id=draft_id,
            current_status=row["review_status"] if row is not None else None,
            requested_action=action,
            proposed_status=ACTION_TO_STATUS.get(action),
            proposed_decision=ACTION_TO_DECISION.get(action),
            dry_run=not apply,
            apply=apply,
            blockers=[f"db_review_update_failed:{exc}"],
            warnings=list(dict.fromkeys(warnings)),
            review_notes=review_notes,
            merge_into_draft_id=merge_into_draft_id,
            affected_rows=affected_rows,
        )
    finally:
        conn.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review a mechanism draft candidate with a dry-run-first K-Q4 contract."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--draft-id", required=True)
    parser.add_argument(
        "--action",
        required=True,
        choices=sorted(ACTION_TO_STATUS),
    )
    parser.add_argument("--review-notes")
    parser.add_argument("--merge-into-draft-id")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true")
    mode_group.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_mechanism_draft_review_report(
        args.db_path,
        draft_id=args.draft_id,
        action=args.action,
        review_notes=args.review_notes,
        merge_into_draft_id=args.merge_into_draft_id,
        apply=bool(args.apply),
    )
    if args.json:
        _print_json(report)
    else:
        print(report)
    return 0 if report["status"] == "OK" else 1


def _fetch_draft(conn: sqlite3.Connection, draft_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT draft_id, review_status, review_decision, review_notes,
               merged_into_draft_id, reviewed_at, updated_at
        FROM mechanism_draft_candidates
        WHERE draft_id = ?
        """,
        (draft_id,),
    ).fetchone()


def _report(
    *,
    status: str,
    draft_id: str,
    current_status: str | None,
    requested_action: str,
    proposed_status: str | None,
    proposed_decision: str | None,
    dry_run: bool,
    apply: bool,
    blockers: list[str],
    warnings: list[str],
    review_notes: str | None = None,
    merge_into_draft_id: str | None = None,
    target_draft_id: str | None = None,
    affected_rows: int = 0,
    reviewed_at: str | None = None,
    db_write_performed: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "mode": MODE,
        "draft_id": draft_id,
        "current_status": current_status,
        "requested_action": requested_action,
        "proposed_status": proposed_status,
        "proposed_decision": proposed_decision,
        "review_notes": review_notes,
        "merge_into_draft_id": merge_into_draft_id,
        "target_draft_id": target_draft_id,
        "dry_run": dry_run,
        "apply": apply,
        "affected_rows": affected_rows,
        "reviewed_at": reviewed_at,
        "blockers": blockers,
        "warnings": warnings,
        "db_write_performed": db_write_performed,
        "mechanism_card_generated": False,
        "mechanism_card_created": False,
        "llm_called": False,
        "api_called": False,
        "vector_store_write_performed": False,
        "knowledge_chunks_write_performed": False,
        "zotero_inspiration_notes_write_performed": False,
        "zotero_source_db_write_performed": False,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _print_json(report: Mapping[str, Any]) -> None:
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        print(output, end="")
        return
    buffer.write(output.encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
