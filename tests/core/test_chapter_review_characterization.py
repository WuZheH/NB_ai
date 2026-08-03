from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from app.core.paths import DEFAULT_DB_PATH

from app.domains.chapter_review import _pipeline_legacy
from app.services import chapter_review_pipeline_service as review_service


_PRODUCTION_ENV_NAMES = (
    review_service.PRODUCTION_REVIEW_SAVE_CANARY_ENV,
    review_service.PRODUCTION_REVIEW_SAVE_SECTION_ENV,
    review_service.PRODUCTION_REVIEW_SAVE_SECTION84_PN68_ENV,
    review_service.PRODUCTION_REVIEW_SAVE_SECTION_TARGET_ENV,
)


@pytest.fixture()
def review_schema_readiness() -> dict[str, Any]:
    """Build only the review schema in a process-local SQLite database."""
    conn = sqlite3.connect(":memory:")
    try:
        review_service.ensure_chapter_review_tables(conn)
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            review_service.NOTE_CORRECTION_REVIEW_TABLE,
            review_service.NOTE_CORRECTION_REVIEW_ITEM_TABLE,
        }.issubset(tables)
    finally:
        conn.close()
    return {
        "review_schema_ready": True,
        "production_review_write_allowed": False,
        "current_blockers": [],
    }


@pytest.fixture(autouse=True)
def clear_production_review_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _PRODUCTION_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _set_production_detection(
    monkeypatch: pytest.MonkeyPatch,
    *,
    is_production: bool,
) -> None:
    gate_globals = review_service.build_note_correction_review_save_request_gate.__globals__
    monkeypatch.setitem(
        gate_globals,
        "_is_default_research_db_path",
        lambda _path: is_production,
    )


def _canary_package(server_ids: list[str]) -> dict[str, Any]:
    return {
        "scope": {
            "canary_subscope": True,
            "is_canary_subscope": True,
            "parent_review_mode": "section_scoped",
            "parent_scope_id": "section_8_2",
            "selected_server_note_ids": list(server_ids),
        },
        "correction_candidates": [
            {"server_note_id": server_id} for server_id in server_ids
        ],
    }


def _call_gate(
    readiness: dict[str, Any],
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "research_db_path": "X:/characterization-only.db",
        "document_id": 10,
        "chapter_id": 69,
        "review_mode": "canary_subscope",
        "canary_subscope": True,
        "package": _canary_package(["zinsp_a"]),
        "selected_server_note_ids": ["zinsp_a"],
        "selected_note_ids": None,
        "confirm_write": True,
        "confirmation_context": review_service.NOTE_CORRECTION_SAVE_CONTEXT,
        "validator_valid": True,
        "human_audit_confirmed": True,
        "validation": {
            "completeness": {"expected_count": 1, "actual_count": 1},
            "stats": {},
        },
        "readiness": readiness,
    }
    values.update(overrides)
    return review_service.build_note_correction_review_save_request_gate(**values)


def _enable_canary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(review_service.PRODUCTION_REVIEW_SAVE_CANARY_ENV, "1")


def _section_gate(
    readiness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: str | None,
    count: int,
) -> dict[str, Any]:
    monkeypatch.setenv(review_service.PRODUCTION_REVIEW_SAVE_SECTION_ENV, "1")
    if target is not None:
        monkeypatch.setenv(
            review_service.PRODUCTION_REVIEW_SAVE_SECTION_TARGET_ENV,
            target,
        )
    server_ids = [f"zinsp_{index}" for index in range(count)]
    scope_id = target or "section_8_2"
    return _call_gate(
        readiness,
        review_mode="section_scoped",
        canary_subscope=False,
        package={
            "scope_id": scope_id,
            "scope": {"scope_id": scope_id, "section_id": scope_id},
            "correction_candidates": [
                {
                    "server_note_id": server_id,
                    "zotero_annotation_key": f"KEY{index}",
                }
                for index, server_id in enumerate(server_ids)
            ],
        },
        selected_server_note_ids=server_ids,
        validation={
            "completeness": {"expected_count": count, "actual_count": count},
            "stats": {},
        },
    )


def _pn68_gate_payload() -> tuple[dict[str, Any], dict[str, Any]]:
    pn68_server_id = review_service.PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID
    server_ids = [pn68_server_id, *[f"zinsp_{index}" for index in range(23)]]
    candidates = [
        {
            "server_note_id": pn68_server_id,
            "zotero_annotation_key": review_service.PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY,
            "warnings": sorted(
                review_service.PRODUCTION_REVIEW_SECTION84_PN68_REQUIRED_WARNINGS
            ),
            "evidence_alignment_status": "unmatched",
            "anchor_method": "unmatched",
        },
        *[
            {
                "server_note_id": server_id,
                "zotero_annotation_key": f"KEY{index}",
            }
            for index, server_id in enumerate(server_ids[1:], 1)
        ],
    ]
    package = {
        "scope_id": "section_8_4",
        "scope": {
            "scope_id": "section_8_4",
            "section_id": "section_8_4",
            "pn68_in_scope": True,
        },
        "correction_candidates": candidates,
    }
    validation = {
        "completeness": {"expected_count": 24, "actual_count": 24},
        "stats": {"pn68yptt_present": True},
        "normalized_preview": [
            {
                "server_note_id": pn68_server_id,
                "zotero_annotation_key": review_service.PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY,
                "correction_status": "unclear",
                "issue_type": "alignment uncertain",
                "reviewer_warning": "unmatched alignment",
                "has_alignment_warning": True,
            }
        ],
    }
    return package, validation


def test_non_production_database_gate_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
    review_schema_readiness: dict[str, Any],
) -> None:
    _set_production_detection(monkeypatch, is_production=False)
    readiness = {**review_schema_readiness, "production_review_write_allowed": True}

    result = _call_gate(readiness)

    assert result == {
        "allowed": True,
        "reason": None,
        "mode": "non_production_review_save",
        "is_production_db": False,
        "request_review_mode": "canary_subscope",
        "request_canary_subscope": True,
        "selected_count": 1,
        "current_blockers": [],
        "allowed_write_tables": [
            review_service.NOTE_CORRECTION_REVIEW_TABLE,
            review_service.NOTE_CORRECTION_REVIEW_ITEM_TABLE,
        ],
    }


def test_production_write_disabled_gate(
    monkeypatch: pytest.MonkeyPatch,
    review_schema_readiness: dict[str, Any],
) -> None:
    _set_production_detection(monkeypatch, is_production=True)

    result = _call_gate(review_schema_readiness)

    assert result["allowed"] is False
    assert result["mode"] == "production_review_save_guarded"
    assert result["reason"] == "production_db_write_disabled"
    assert result["current_blockers"] == ["production_db_write_disabled"]


def test_canary_missing_confirmation_context(
    monkeypatch: pytest.MonkeyPatch,
    review_schema_readiness: dict[str, Any],
) -> None:
    _set_production_detection(monkeypatch, is_production=True)
    _enable_canary(monkeypatch)

    result = _call_gate(review_schema_readiness, confirmation_context=None)

    assert result["mode"] == "production_review_save_canary"
    assert result["current_blockers"] == ["confirmation_context_invalid"]


@pytest.mark.parametrize("server_ids", [[], [f"zinsp_{i}" for i in range(4)]])
def test_canary_selected_count_must_be_between_one_and_three(
    monkeypatch: pytest.MonkeyPatch,
    review_schema_readiness: dict[str, Any],
    server_ids: list[str],
) -> None:
    _set_production_detection(monkeypatch, is_production=True)
    _enable_canary(monkeypatch)

    result = _call_gate(
        review_schema_readiness,
        package=_canary_package(server_ids),
        selected_server_note_ids=server_ids,
    )

    assert result["current_blockers"] == [
        "production_canary_selected_count_must_be_1_3"
    ]


def test_section_target_missing_blocker_order(
    monkeypatch: pytest.MonkeyPatch,
    review_schema_readiness: dict[str, Any],
) -> None:
    _set_production_detection(monkeypatch, is_production=True)

    result = _section_gate(
        review_schema_readiness,
        monkeypatch,
        target=None,
        count=10,
    )

    assert result["current_blockers"] == [
        "production_section_target_required",
        "production_section_expected_count_mismatch",
    ]


def test_deferred_section_remains_blocked(
    monkeypatch: pytest.MonkeyPatch,
    review_schema_readiness: dict[str, Any],
) -> None:
    _set_production_detection(monkeypatch, is_production=True)

    result = _section_gate(
        review_schema_readiness,
        monkeypatch,
        target="section_8_3",
        count=10,
    )

    assert result["current_blockers"] == [
        "production_section_target_deferred",
        "production_section_expected_count_mismatch",
    ]


def test_section_expected_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    review_schema_readiness: dict[str, Any],
) -> None:
    _set_production_detection(monkeypatch, is_production=True)

    result = _section_gate(
        review_schema_readiness,
        monkeypatch,
        target="section_8_2",
        count=9,
    )

    assert result["current_blockers"] == [
        "production_section_expected_count_mismatch"
    ]


def test_pn68_gate_preserves_special_policy(
    monkeypatch: pytest.MonkeyPatch,
    review_schema_readiness: dict[str, Any],
) -> None:
    _set_production_detection(monkeypatch, is_production=True)
    monkeypatch.setenv(
        review_service.PRODUCTION_REVIEW_SAVE_SECTION84_PN68_ENV,
        "1",
    )
    monkeypatch.setenv(
        review_service.PRODUCTION_REVIEW_SAVE_SECTION_TARGET_ENV,
        review_service.PRODUCTION_REVIEW_SECTION84_PN68_SCOPE_ID,
    )
    package, validation = _pn68_gate_payload()

    allowed = _call_gate(
        review_schema_readiness,
        review_mode="section_scoped",
        canary_subscope=False,
        package=package,
        selected_server_note_ids=[],
        validation=validation,
    )

    assert allowed["mode"] == "production_review_save_section84_pn68"
    assert allowed["allowed"] is True
    assert allowed["current_blockers"] == []
    assert allowed["pn68_warning_preserved"] is True

    package["correction_candidates"][0]["warnings"] = []
    blocked = _call_gate(
        review_schema_readiness,
        review_mode="section_scoped",
        canary_subscope=False,
        package=package,
        selected_server_note_ids=[],
        validation=validation,
    )
    assert blocked["current_blockers"] == [
        "production_section84_pn68_source_warning_required"
    ]
    assert blocked["pn68_warning_preserved"] is False


@pytest.mark.parametrize(
    ("override", "blocker"),
    [
        ({"validator_valid": False}, "validator_failed"),
        ({"confirm_write": False}, "confirm_write_required"),
        ({"confirmation_context": "wrong"}, "confirmation_context_invalid"),
        ({"human_audit_confirmed": False}, "human_audit_invalid"),
    ],
)
def test_terminal_review_gates_remain_independent(
    monkeypatch: pytest.MonkeyPatch,
    review_schema_readiness: dict[str, Any],
    override: dict[str, Any],
    blocker: str,
) -> None:
    _set_production_detection(monkeypatch, is_production=True)
    _enable_canary(monkeypatch)

    result = _call_gate(review_schema_readiness, **override)

    assert result["current_blockers"] == [blocker]


def test_canary_blocker_order_is_stable(
    monkeypatch: pytest.MonkeyPatch,
    review_schema_readiness: dict[str, Any],
) -> None:
    _set_production_detection(monkeypatch, is_production=True)
    _enable_canary(monkeypatch)

    result = _call_gate(
        review_schema_readiness,
        review_mode="full_chapter",
        canary_subscope=False,
        selected_server_note_ids=["zinsp_dup", "zinsp_dup"],
        selected_note_ids=["legacy-id"],
        package={
            "scope": {"selected_server_note_ids": ["zinsp_other"]},
            "correction_candidates": [{"server_note_id": ""}],
        },
        validator_valid=False,
        confirm_write=False,
        confirmation_context="wrong",
        human_audit_confirmed=False,
    )

    assert result["current_blockers"] == [
        "production_canary_review_mode_required",
        "production_canary_subscope_confirmation_required",
        "production_canary_selected_note_ids_alias_forbidden",
        "production_canary_selected_server_note_ids_must_be_unique",
        "production_canary_package_scope_required",
        "production_canary_parent_scope_required",
        "production_canary_selected_server_note_ids_mismatch",
        "production_canary_legal_server_note_ids_required",
        "validator_failed",
        "confirm_write_required",
        "confirmation_context_invalid",
        "human_audit_invalid",
    ]


def test_review_pipeline_safety_flags_are_stable() -> None:
    flags = review_service.review_pipeline_safety_flags()
    for key in (
        "db_write_performed",
        "core_db_write_performed",
        "zotero_db_write_performed",
        "vector_store_write_performed",
        "llm_called",
        "external_llm_called",
        "object_candidates_generated",
        "relation_candidates_generated",
        "relation_generated",
        "insight_cards_generated",
        "mechanism_generated",
        "mechanism_draft_written",
        "ocr_or_marker_performed",
    ):
        assert flags[key] is False

    write_flags = review_service.review_pipeline_safety_flags(
        db_write_performed=True
    )
    assert write_flags["db_write_performed"] is True
    assert write_flags["core_db_write_performed"] is True
    explicit_core = review_service.review_pipeline_safety_flags(
        db_write_performed=True,
        core_db_write_performed=False,
    )
    assert explicit_core["core_db_write_performed"] is False


def test_legacy_facade_public_surface_and_signatures_are_stable() -> None:
    assert (
        review_service.save_chapter_note_correction_review
        is _pipeline_legacy.save_chapter_note_correction_review
    )
    public_names = sorted(
        name for name in vars(review_service) if not name.startswith("_")
    )
    assert len(public_names) == 149
    assert hashlib.sha256("\n".join(public_names).encode("utf-8")).hexdigest() == (
        "67ee7a2ca731c07e346a2a30306f1a7b11445b1c1edb4ad4a0b8b4ba8a59d37b"
    )

    owned_contract = []
    for name, value in vars(review_service).items():
        if (
            name.startswith("_")
            or getattr(value, "__module__", None) != review_service.__name__
            or not callable(value)
        ):
            continue
        signature = str(inspect.signature(value)).replace(
            repr(DEFAULT_DB_PATH),
            "Path('<SEARCH_DATA_DIR>/db/research_memory.db')",
        )
        owned_contract.append((name, signature, type(value).__name__))
    encoded = json.dumps(
        sorted(owned_contract),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(owned_contract) == 68
    assert hashlib.sha256(encoded).hexdigest() == (
        "624f70aa8029610e5bc3d27dbfff903427eb33063d77e2f3216e78e1316e9def"
    )
