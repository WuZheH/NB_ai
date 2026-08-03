from __future__ import annotations

import json
import math
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.import_operation_journal import (
    SCHEMA_VERSION,
    ImportOperationJournal,
    ImportOperationJournalStore,
    JournalConflictError,
    JournalValidationError,
    JournalWriteError,
    _deep_freeze,
    _deep_thaw,
    _get_store_lock,
    _store_lock_key,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_VALID_OP_ID = "a" * 32
_VALID_SHA = "b" * 64
_DIGEST_Z = "0" * 63 + "1"
_DIGEST_W = "0" * 63 + "2"
_DIGEST_M = "0" * 63 + "3"
_DIGEST_N = "0" * 63 + "4"
_DIGEST_P = "0" * 63 + "5"
_DIGEST_Q = "0" * 63 + "6"
_DIGEST_U = "0" * 63 + "7"
_DIGEST_X = "0" * 63 + "8"
_DIGEST_R = "0" * 63 + "9"
_DIGEST_S = "1" + "0" * 63


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts(hour: int) -> str:
    return f"2026-07-31T{hour:02d}:00:00+00:00"


def _make_record(**overrides: object) -> ImportOperationJournal:
    kwargs: dict[str, object] = {
        "operation_id": uuid.uuid4().hex,
        "operation_type": "import_document",
        "confirmation_token_digest": _VALID_SHA,
        "transaction_fingerprint": "x" * 32,
        "source_revision_fingerprint": "y" * 32,
        "title": "Test Book",
        "zotero_item_key": "AFMVJLNL",
        "zotero_attachment_key": "KTKTKHCS",
        "source_pdf_sha256": "c" * 64,
        "owner_process_id": os.getpid(),
        "owner_process_started_at": _ts(10),
        "owner_thread_id": 1,
        "started_at": _ts(10),
        "updated_at": _ts(10),
        "heartbeat_at": _ts(10),
        "revision": 0,
        "status": "accepted",
        "stage": "confirmation_accepted",
    }
    kwargs.update(overrides)
    return ImportOperationJournal(**kwargs)  # type: ignore[arg-type]


def _store(tmp_path: Path) -> ImportOperationJournalStore:
    return ImportOperationJournalStore(tmp_path / "operation_journal")


# ===================================================================
# create / read round-trip
# ===================================================================


def test_create_read_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    created = store.create(rec)
    assert created.operation_id == rec.operation_id
    assert created.revision == 0
    assert created.writes_performed is None
    loaded = store.read(rec.operation_id)
    assert loaded is not None
    assert loaded.operation_id == rec.operation_id
    assert loaded.writes_performed is None


def test_create_duplicate_operation_id_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalConflictError, match="already exists"):
        store.create(rec)


def test_read_missing_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.read(_VALID_OP_ID) is None


# ===================================================================
# create initial-state constraints
# ===================================================================


def test_create_rejects_running(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record(status="running", stage="body_import_started")
    with pytest.raises(JournalValidationError, match="create requires status"):
        store.create(rec)


def test_create_rejects_committed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record(
        status="committed", stage="receipt_persisted",
        writes_performed=True, document_id=1, chunk_count=1,
        completion_receipt={"doc": 1},
    )
    with pytest.raises(JournalValidationError, match="create requires status"):
        store.create(rec)


def test_create_rejects_failed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record(
        status="failed", stage="receipt_persisted",
        error={"code": "e"}, completion_receipt={"s": "f"},
    )
    with pytest.raises(JournalValidationError, match="create requires status"):
        store.create(rec)


def test_create_rejects_orphaned(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record(status="orphaned", error={"code": "e"})
    with pytest.raises(JournalValidationError, match="create requires status"):
        store.create(rec)


def test_create_rejects_writes_performed_true(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(JournalValidationError, match="writes_performed"):
        _make_record(writes_performed=True)


def test_create_rejects_writes_performed_false(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(JournalValidationError, match="writes_performed"):
        _make_record(writes_performed=False)


# ===================================================================
# operation_id validation
# ===================================================================


def test_operation_id_rejects_path_traversal_at_construct() -> None:
    for bad in ("../../outside", "..\\outside", "a/b", ".."):
        with pytest.raises(JournalValidationError, match="operation_id"):
            ImportOperationJournal(
                operation_id=bad,
                confirmation_token_digest=_VALID_SHA,
                source_pdf_sha256=_VALID_SHA,
                transaction_fingerprint="x",
                source_revision_fingerprint="y",
            )


def test_operation_id_rejects_uppercase() -> None:
    with pytest.raises(JournalValidationError, match="operation_id"):
        ImportOperationJournal(
            operation_id="A" * 32,
            confirmation_token_digest=_VALID_SHA,
            source_pdf_sha256=_VALID_SHA,
            transaction_fingerprint="x",
            source_revision_fingerprint="y",
        )


def test_operation_id_rejects_wrong_length() -> None:
    for length in (31, 33):
        with pytest.raises(JournalValidationError, match="operation_id"):
            ImportOperationJournal(
                operation_id="a" * length,
                confirmation_token_digest=_VALID_SHA,
                source_pdf_sha256=_VALID_SHA,
                transaction_fingerprint="x",
                source_revision_fingerprint="y",
            )


def test_from_dict_rejects_invalid_operation_id() -> None:
    with pytest.raises(JournalValidationError, match="operation_id"):
        ImportOperationJournal.from_dict(
            {
                "operation_id": "not-hex!!",
                "confirmation_token_digest": _VALID_SHA,
                "source_pdf_sha256": _VALID_SHA,
                "transaction_fingerprint": "x",
                "source_revision_fingerprint": "y",
                "started_at": _ts(10),
                "updated_at": _ts(10),
                "revision": 0,
                "status": "accepted",
                "stage": "confirmation_accepted",
            }
        )


def test_read_rejects_operation_id_filename_mismatch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    wrong_path = store._journal_dir / f"{'f' * 32}.json"
    wrong_path.write_text(
        json.dumps(
            {
                "operation_id": "e" * 32,
                "confirmation_token_digest": _VALID_SHA,
                "source_pdf_sha256": _VALID_SHA,
                "transaction_fingerprint": "x",
                "source_revision_fingerprint": "y",
                "started_at": _ts(10),
                "updated_at": _ts(10),
                "revision": 0,
                "status": "accepted",
                "stage": "confirmation_accepted",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(JournalValidationError, match="operation_id"):
        store.read("f" * 32)


# ===================================================================
# SHA-256 field validation
# ===================================================================


def test_confirmation_token_digest_strict_64_hex() -> None:
    for bad in ("", "a" * 63, "a" * 65, "g" * 64, "A" * 64, "a b" + "c" * 61):
        with pytest.raises(JournalValidationError, match="confirmation_token_digest"):
            ImportOperationJournal(
                operation_id=_VALID_OP_ID,
                confirmation_token_digest=bad,
                source_pdf_sha256=_VALID_SHA,
                transaction_fingerprint="x",
                source_revision_fingerprint="y",
            )


def test_source_pdf_sha256_strict_64_hex() -> None:
    for bad in ("", "a" * 63, "A" * 64, "g" * 64):
        with pytest.raises(JournalValidationError, match="source_pdf_sha256"):
            ImportOperationJournal(
                operation_id=_VALID_OP_ID,
                confirmation_token_digest=_VALID_SHA,
                source_pdf_sha256=bad,
                transaction_fingerprint="x",
                source_revision_fingerprint="y",
            )


# ===================================================================
# timestamp validation
# ===================================================================


def test_timestamp_rejects_naive() -> None:
    with pytest.raises(JournalValidationError, match="naive"):
        ImportOperationJournal(
            operation_id=_VALID_OP_ID,
            confirmation_token_digest=_VALID_SHA,
            source_pdf_sha256=_VALID_SHA,
            transaction_fingerprint="x",
            source_revision_fingerprint="y",
            started_at="2026-07-31T10:00:00",
            updated_at="2026-07-31T11:00:00+00:00",
        )


def test_timestamp_rejects_garbage() -> None:
    with pytest.raises(JournalValidationError, match="not valid ISO-8601"):
        _make_record(started_at="not-a-date")


def test_timestamp_accepts_z_and_normalizes() -> None:
    rec = _make_record(
        started_at="2026-07-31T10:00:00Z",
        updated_at="2026-07-31T11:00:00Z",
    )
    assert "+00:00" in rec.started_at
    assert "Z" not in rec.started_at


def test_updated_at_before_started_at_rejected() -> None:
    with pytest.raises(JournalValidationError, match="earlier than started_at"):
        _make_record(
            started_at="2026-07-31T12:00:00+00:00",
            updated_at="2026-07-31T10:00:00+00:00",
        )


def test_heartbeat_at_before_started_at_rejected() -> None:
    with pytest.raises(JournalValidationError, match="earlier than started_at"):
        _make_record(
            started_at="2026-07-31T12:00:00+00:00",
            heartbeat_at="2026-07-31T10:00:00+00:00",
        )


def test_heartbeat_at_can_be_none() -> None:
    rec = _make_record(heartbeat_at=None)  # type: ignore[arg-type]
    assert rec.heartbeat_at == ""


def test_started_at_empty_rejected() -> None:
    with pytest.raises(JournalValidationError, match="Timestamp must be"):
        ImportOperationJournal(
            operation_id=_VALID_OP_ID,
            confirmation_token_digest=_VALID_SHA,
            source_pdf_sha256=_VALID_SHA,
            transaction_fingerprint="x",
            source_revision_fingerprint="y",
            started_at="",
            updated_at=_ts(10),
        )


def test_int_heartbeat_at_rejected() -> None:
    with pytest.raises(JournalValidationError, match="Timestamp must be a string"):
        _make_record(heartbeat_at=123)  # type: ignore[arg-type]


# ===================================================================
# required string fields — whitespace rejection
# ===================================================================


def test_empty_transaction_fingerprint_rejected() -> None:
    with pytest.raises(JournalValidationError, match="transaction_fingerprint"):
        _make_record(transaction_fingerprint="")


def test_whitespace_transaction_fingerprint_rejected() -> None:
    with pytest.raises(JournalValidationError, match="transaction_fingerprint"):
        _make_record(transaction_fingerprint="   ")


def test_whitespace_source_revision_fingerprint_rejected() -> None:
    with pytest.raises(JournalValidationError, match="source_revision_fingerprint"):
        _make_record(source_revision_fingerprint="\t\n")


# ===================================================================
# type validation
# ===================================================================


def test_bool_revision_rejected() -> None:
    with pytest.raises(JournalValidationError, match="revision must be an int"):
        _make_record(revision=True)  # type: ignore[arg-type]


def test_negative_chunk_count_rejected() -> None:
    with pytest.raises(JournalValidationError, match="chunk_count must be"):
        _make_record(chunk_count=-1)


def test_create_revision_nonzero_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record(revision=5)
    with pytest.raises(JournalValidationError, match="revision must be 0"):
        store.create(rec)


# ===================================================================
# writes_performed tri-state
# ===================================================================


def test_accepted_default_writes_performed_none() -> None:
    rec = _make_record()
    assert rec.writes_performed is None


def test_committed_requires_writes_performed_true() -> None:
    with pytest.raises(JournalValidationError, match="writes_performed"):
        _make_record(
            status="committed", stage="receipt_persisted",
            writes_performed=None, document_id=1, chunk_count=1,
            completion_receipt={"doc": 1},
        )


def test_committed_requires_document_id_positive() -> None:
    with pytest.raises(JournalValidationError, match="document_id"):
        _make_record(
            status="committed", stage="receipt_persisted",
            writes_performed=True, chunk_count=1,
            completion_receipt={"doc": 1},
        )


def test_committed_requires_chunk_count_positive() -> None:
    with pytest.raises(JournalValidationError, match="chunk_count"):
        _make_record(
            status="committed", stage="receipt_persisted",
            writes_performed=True, document_id=3,
            completion_receipt={"doc": 1},
        )


def test_failed_allows_writes_performed_none() -> None:
    rec = _make_record(
        status="failed", stage="receipt_persisted",
        writes_performed=None,
        error={"code": "e"},
        completion_receipt={"status": "failed"},
    )
    assert rec.writes_performed is None


# ===================================================================
# identity fields immutable in update
# ===================================================================


def test_transaction_fingerprint_update_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalValidationError, match="transaction_fingerprint must not"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="body_import_started",
                      transaction_fingerprint="new")


def test_source_revision_fingerprint_update_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalValidationError, match="source_revision_fingerprint must not"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="body_import_started",
                      source_revision_fingerprint="new")


def test_source_pdf_sha256_update_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalValidationError, match="source_pdf_sha256 must not"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="body_import_started",
                      source_pdf_sha256="c" * 64)


def test_zotero_keys_update_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    for field in ("zotero_item_key", "zotero_attachment_key"):
        with pytest.raises(JournalValidationError, match=f"{field} must not"):
            store.update(rec.operation_id, expected_revision=0,
                          **{field: "new"})


def test_owner_fields_update_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    for field in ("owner_process_id", "owner_thread_id"):
        with pytest.raises(JournalValidationError, match=f"{field} must not"):
            store.update(rec.operation_id, expected_revision=0,
                          **{field: 999})


def test_updated_at_by_caller_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalValidationError, match="updated_at must not"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="body_import_started",
                      updated_at=_ts(12))


# ===================================================================
# expected_revision strict validation
# ===================================================================


def test_expected_revision_false_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalValidationError, match="expected_revision"):
        store.update(rec.operation_id, expected_revision=False)  # type: ignore[arg-type]


def test_expected_revision_true_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalValidationError, match="expected_revision"):
        store.update(rec.operation_id, expected_revision=True)  # type: ignore[arg-type]


def test_expected_revision_negative_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalValidationError, match="expected_revision"):
        store.update(rec.operation_id, expected_revision=-1)


def test_expected_status_invalid_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalValidationError, match="Invalid expected_status"):
        store.update(rec.operation_id, expected_revision=0,
                      expected_status="bogus")


# ===================================================================
# deterministic sorting
# ===================================================================


def test_find_by_token_digest_sorted_by_started_at_then_op_id(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    digest = _DIGEST_Z
    store._ensure_dir()
    for op_id, started in [("b" * 32, _ts(10)), ("a" * 32, _ts(10)), ("c" * 32, _ts(9))]:
        rec = _make_record(confirmation_token_digest=digest,
                           operation_id=op_id, started_at=started)
        store._write_atomic(rec.to_dict(), store._journal_path(op_id))
    results = store.find_by_token_digest(digest)
    assert len(results) == 3
    assert results[0].operation_id == "c" * 32
    assert results[1].operation_id == "a" * 32
    assert results[2].operation_id == "b" * 32


def test_find_by_document_id_is_validated_and_deterministic(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    for op_id, started, document_id in (
        ("b" * 32, _ts(10), 7),
        ("a" * 32, _ts(10), 7),
        ("c" * 32, _ts(9), 7),
        ("d" * 32, _ts(8), 8),
    ):
        record = _make_record(
            operation_id=op_id,
            started_at=started,
            status="committed",
            stage="receipt_persisted",
            writes_performed=True,
            document_id=document_id,
            chunk_count=1,
            completion_receipt={"kind": "success"},
        )
        store._write_atomic(record.to_dict(), store._journal_path(op_id))

    assert [
        record.operation_id for record in store.find_by_document_id(7)
    ] == ["c" * 32, "a" * 32, "b" * 32]
    assert store.find_by_document_id(9) == []
    for invalid in (0, -1, True, False):
        with pytest.raises(JournalValidationError, match="document_id"):
            store.find_by_document_id(invalid)  # type: ignore[arg-type]


def test_find_by_document_id_fails_closed_on_malformed_entry(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    store._journal_path("a" * 32).write_text("{bad", encoding="utf-8")

    with pytest.raises(JournalValidationError):
        store.find_by_document_id(1)


def test_find_latest_tiebreaker_uses_operation_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    digest = _DIGEST_W
    store._ensure_dir()
    for op_id in ("b" * 32, "a" * 32):
        rec = _make_record(
            confirmation_token_digest=digest, operation_id=op_id,
            status="committed", stage="receipt_persisted",
            writes_performed=True, document_id=1, chunk_count=1,
            updated_at=_ts(12), completion_receipt={"doc": 1},
        )
        store._write_atomic(rec.to_dict(), store._journal_path(op_id))
    result = store.find_latest_by_token_digest(digest)
    assert result is not None
    assert result.operation_id == "b" * 32


# ===================================================================
# nested secret rejection
# ===================================================================


def test_error_rejects_nested_authorization() -> None:
    with pytest.raises(JournalValidationError, match="Forbidden secret key"):
        _make_record(
            status="failed", stage="receipt_persisted",
            error={"authorization": "Bearer SECRET"},
            completion_receipt={"status": "failed"},
        )


def test_warnings_rejects_nested_confirmation_token() -> None:
    with pytest.raises(JournalValidationError, match="Forbidden secret key"):
        _make_record(
            status="failed", stage="receipt_persisted",
            error={"code": "test"},
            completion_receipt={"status": "failed"},
            warnings=[{"nested": {"confirmation_token": "RAW"}}],
        )


def test_completion_receipt_rejects_nested_api_key() -> None:
    with pytest.raises(JournalValidationError, match="Forbidden secret key"):
        _make_record(
            status="failed", stage="receipt_persisted",
            error={"code": "test"},
            completion_receipt={"api_key": "SECRET"},
        )


def test_confirmation_token_digest_not_falsely_rejected() -> None:
    rec = _make_record(
        status="failed", stage="receipt_persisted",
        error={"error_code": "test", "confirmation_token_digest": _VALID_SHA},
        warnings=[{"confirmation_token_digest": _VALID_SHA}],
        completion_receipt={"status": "failed"},
    )
    assert rec.error is not None
    assert "confirmation_token_digest" in rec.error


def test_token_consumed_not_falsely_rejected() -> None:
    rec = _make_record(
        status="committed", stage="receipt_persisted",
        writes_performed=True, document_id=3, chunk_count=100,
        completion_receipt={"token_consumed": True},
    )
    assert rec.completion_receipt is not None
    assert rec.completion_receipt["token_consumed"] is True


def test_secret_match_is_case_insensitive() -> None:
    with pytest.raises(JournalValidationError, match="Forbidden secret key"):
        _make_record(
            status="failed", stage="receipt_persisted",
            error={"Authorization": "Bearer SECRET"},
            completion_receipt={"status": "failed"},
        )
    with pytest.raises(JournalValidationError, match="Forbidden secret key"):
        _make_record(
            status="failed", stage="receipt_persisted",
            error={"API_KEY": "SECRET"},
            completion_receipt={"status": "failed"},
        )


def test_secret_match_trims_whitespace() -> None:
    with pytest.raises(JournalValidationError, match="Forbidden secret key"):
        _make_record(
            status="failed", stage="receipt_persisted",
            error={"  authorization  ": "SECRET"},
            completion_receipt={"status": "failed"},
        )


# ===================================================================
# NaN / Infinity rejection
# ===================================================================


def test_nan_rejected_in_error() -> None:
    with pytest.raises(JournalValidationError, match="NaN and Infinity"):
        _make_record(
            status="failed", stage="receipt_persisted",
            error={"value": float("nan")},
            completion_receipt={"status": "failed"},
        )


def test_nan_rejected_in_nested_list() -> None:
    with pytest.raises(JournalValidationError, match="NaN and Infinity"):
        _make_record(
            status="failed", stage="receipt_persisted",
            error={"items": [1.0, 2.0, float("nan")]},
            completion_receipt={"status": "failed"},
        )


# ===================================================================
# nested immutability
# ===================================================================


def test_input_dict_mutations_do_not_affect_record() -> None:
    mutable_error = {"code": "original"}
    rec = _make_record(
        status="failed", stage="receipt_persisted",
        error=mutable_error,
        completion_receipt={"status": "failed"},
    )
    mutable_error["code"] = "MUTATED"
    assert rec.error is not None
    assert rec.error["code"] == "original"


def test_record_mapping_cannot_be_modified() -> None:
    rec = _make_record(
        status="failed", stage="receipt_persisted",
        error={"code": "test"},
        completion_receipt={"status": "failed"},
    )
    assert rec.error is not None
    with pytest.raises(TypeError):
        rec.error["extra"] = "MUTATED"  # type: ignore[index]


def test_record_warnings_cannot_append() -> None:
    rec = _make_record(
        warnings=[{"msg": "w1"}],
        status="running", stage="body_import_started",
    )
    with pytest.raises(AttributeError):
        rec.warnings.append({"msg": "MUTATED"})  # type: ignore[union-attr]


def test_to_dict_mutations_do_not_affect_record() -> None:
    rec = _make_record(
        status="failed", stage="receipt_persisted",
        error={"code": "test"},
        completion_receipt={"status": "failed"},
    )
    d = rec.to_dict()
    d["error"]["extra"] = "MUTATED"
    assert rec.error is not None
    assert "extra" not in rec.error


def test_deep_freeze_handles_mappingproxy_nested_list() -> None:
    """MappingProxyType with nested list is deeply frozen to tuple."""
    data = {"items": [{"a": 1}, {"b": 2}]}
    frozen = _deep_freeze(data)
    # Outer is MappingProxyType, inner list is tuple.
    thawed = _deep_thaw(frozen)
    assert thawed == {"items": [{"a": 1}, {"b": 2}]}
    assert isinstance(thawed, dict)
    assert isinstance(thawed["items"], list)


def test_to_dict_nested_mappingproxy_mutations_do_not_affect_record() -> None:
    rec = _make_record(
        status="failed", stage="receipt_persisted",
        error={"nested": {"deep": "value"}},
        completion_receipt={"status": "failed", "nested": {"x": 1}},
    )
    d = rec.to_dict()
    d["error"]["nested"]["deep"] = "MUTATED"
    d["completion_receipt"]["nested"]["x"] = 999
    assert rec.error is not None
    assert "deep" in rec.error["nested"]
    assert rec.error["nested"]["deep"] == "value"


# ===================================================================
# read() edge cases
# ===================================================================


def test_read_rejects_non_utf8(tmp_path: Path) -> None:
    store = _store(tmp_path)
    op_id = "f" * 32
    path = store._journal_dir / f"{op_id}.json"
    store._journal_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe\x00\x00")
    with pytest.raises(JournalValidationError, match="not valid UTF-8"):
        store.read(op_id)


def test_read_rejects_symlink_monkeypatch(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    op_id = "e" * 32
    store._journal_dir.mkdir(parents=True, exist_ok=True)
    path = store._journal_dir / f"{op_id}.json"
    path.write_text(
        json.dumps(
            {
                "operation_id": op_id,
                "confirmation_token_digest": _VALID_SHA,
                "source_pdf_sha256": _VALID_SHA,
                "transaction_fingerprint": "x",
                "source_revision_fingerprint": "y",
                "started_at": _ts(10),
                "updated_at": _ts(10),
                "revision": 0,
                "status": "accepted",
                "stage": "confirmation_accepted",
            }
        ),
        encoding="utf-8",
    )
    original_is_symlink = Path.is_symlink

    def _fake_is_symlink(self):  # type: ignore[no-untyped-def]
        if self == path:
            return True
        return original_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", _fake_is_symlink)
    with pytest.raises(JournalValidationError, match="symlink"):
        store.read(op_id)


def test_read_rejects_directory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    op_id = "d" * 32
    dir_path = store._journal_dir / f"{op_id}.json"
    dir_path.mkdir(parents=True)
    with pytest.raises(JournalValidationError, match="not a regular file"):
        store.read(op_id)


def test_read_rejects_truncated_json(tmp_path: Path) -> None:
    store = _store(tmp_path)
    op_id = "c" * 32
    store._journal_dir.mkdir(parents=True, exist_ok=True)
    path = store._journal_dir / f"{op_id}.json"
    path.write_text(
        '{"operation_id": "cccccccccccccccccccccccccccccccc", '
        '"confirmation_token_digest',
        encoding="utf-8",
    )
    with pytest.raises(JournalValidationError, match="not valid JSON"):
        store.read(op_id)


def test_read_rejects_json_array_root(tmp_path: Path) -> None:
    store = _store(tmp_path)
    op_id = "b" * 32
    store._journal_dir.mkdir(parents=True, exist_ok=True)
    path = store._journal_dir / f"{op_id}.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(JournalValidationError, match="must be a JSON object"):
        store.read(op_id)


def test_read_rejects_bad_schema_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    op_id = "a" * 32
    store._journal_dir.mkdir(parents=True, exist_ok=True)
    path = store._journal_dir / f"{op_id}.json"
    path.write_text(
        json.dumps(
            {
                "operation_id": op_id,
                "schema_version": "future.v99",
                "confirmation_token_digest": _VALID_SHA,
                "source_pdf_sha256": _VALID_SHA,
                "transaction_fingerprint": "x",
                "source_revision_fingerprint": "y",
                "started_at": _ts(10),
                "updated_at": _ts(10),
                "revision": 0,
                "status": "accepted",
                "stage": "confirmation_accepted",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(JournalValidationError, match="schema_version"):
        store.read(op_id)


# ===================================================================
# JSON format
# ===================================================================


def test_json_file_ends_with_newline(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    path = store._journal_dir / f"{rec.operation_id}.json"
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_json_uses_ensure_ascii_false(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record(title="算法导论")
    store.create(rec)
    path = store._journal_dir / f"{rec.operation_id}.json"
    assert "算法导论" in path.read_text(encoding="utf-8")


# ===================================================================
# revision tests
# ===================================================================


def test_revision_starts_at_zero() -> None:
    rec = _make_record()
    assert rec.revision == 0


def test_revision_increments_on_update(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    u1 = store.update(rec.operation_id, expected_revision=0,
                       status="running", stage="body_import_started")
    assert u1.revision == 1
    u2 = store.update(rec.operation_id, expected_revision=1,
                       status="running", stage="body_import_completed")
    assert u2.revision == 2


def test_stale_expected_revision_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    store.update(rec.operation_id, expected_revision=0,
                  status="running", stage="body_import_started")
    with pytest.raises(JournalConflictError, match="Expected revision"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="body_import_completed")


# ===================================================================
# shared lock tests
# ===================================================================


def test_two_stores_share_lock(tmp_path: Path) -> None:
    d = tmp_path / "journals"
    s1 = ImportOperationJournalStore(d)
    s2 = ImportOperationJournalStore(d)
    assert s1._lock is s2._lock


def test_two_threads_same_revision_one_succeeds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    results = []
    barrier = threading.Barrier(2, timeout=5)

    def worker(label):  # type: ignore[no-untyped-def]
        barrier.wait()
        try:
            r = store.update(rec.operation_id, expected_revision=0,
                              status="running", stage="body_import_started")
            results.append((label, "ok", r.revision))
        except JournalConflictError as e:
            results.append((label, "conflict", str(e)[:80]))

    t1 = threading.Thread(target=worker, args=("A",))
    t2 = threading.Thread(target=worker, args=("B",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    oks = [r for r in results if r[1] == "ok"]
    conflicts = [r for r in results if r[1] == "conflict"]
    assert len(oks) == 1
    assert len(conflicts) == 1
    assert oks[0][2] == 1


def test_two_threads_create_same_op_id_one_succeeds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    op_id = "d" * 32
    results = []
    barrier = threading.Barrier(2, timeout=5)

    def worker():  # type: ignore[no-untyped-def]
        barrier.wait()
        try:
            rec = _make_record(operation_id=op_id)
            store.create(rec)
            results.append("ok")
        except JournalConflictError:
            results.append("conflict")

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert results.count("ok") == 1
    assert results.count("conflict") == 1


def test_two_threads_create_same_digest_one_succeeds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    digest = _DIGEST_R
    results = []
    barrier = threading.Barrier(2, timeout=5)

    def worker():  # type: ignore[no-untyped-def]
        barrier.wait()
        try:
            rec = _make_record(confirmation_token_digest=digest)
            store.create(rec)
            results.append("ok")
        except JournalConflictError:
            results.append("conflict")

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert results.count("ok") == 1
    assert results.count("conflict") == 1


# ===================================================================
# lock key OS-aware
# ===================================================================


def test_lock_key_casefold_windows(tmp_path) -> None:
    a = tmp_path / "Journals"
    b = tmp_path / "journals"
    if os.name == "nt":
        assert _store_lock_key(a) == _store_lock_key(b)
    else:
        assert _store_lock_key(a) != _store_lock_key(b)


def test_lock_key_same_on_windows_different_case(tmp_path) -> None:
    a = tmp_path / "OperationJournal"
    b = tmp_path / "operationjournal"
    k1 = _store_lock_key(a)
    k2 = _store_lock_key(b)
    if os.name == "nt":
        assert k1 == k2
    else:
        assert k1 != k2


# ===================================================================
# digest uniqueness in create
# ===================================================================


def test_create_rejects_same_digest_different_op_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    digest = _DIGEST_S
    r1 = _make_record(confirmation_token_digest=digest)
    store.create(r1)
    r2 = _make_record(confirmation_token_digest=digest)
    with pytest.raises(JournalConflictError, match="already exists for confirmation"):
        store.create(r2)


# ===================================================================
# resolve_by_token_digest
# ===================================================================


def test_resolve_zero_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.resolve_by_token_digest(_DIGEST_U) is None


def test_resolve_one_returns_record(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record(confirmation_token_digest=_DIGEST_P)
    store.create(rec)
    result = store.resolve_by_token_digest(_DIGEST_P)
    assert result is not None
    assert result.operation_id == rec.operation_id


def test_resolve_multiple_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    for op_id in ("a" * 32, "b" * 32):
        rec = _make_record(confirmation_token_digest=_DIGEST_Q, operation_id=op_id)
        store._write_atomic(rec.to_dict(), store._journal_path(op_id))
    with pytest.raises(JournalConflictError, match="Multiple journals"):
        store.resolve_by_token_digest(_DIGEST_Q)


# ===================================================================
# status-stage cross-validation
# ===================================================================


def test_body_import_started_cannot_directly_committed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    rec = _make_record(status="running", stage="body_import_started")
    store._write_atomic(rec.to_dict(), store._journal_path(rec.operation_id))
    with pytest.raises(JournalValidationError, match="final_verification_completed"):
        store.update(rec.operation_id, expected_revision=0,
                      status="committed", stage="receipt_persisted",
                      writes_performed=True, document_id=3, chunk_count=100,
                      completion_receipt={"doc": 3})


def test_publish_completed_cannot_directly_committed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    rec = _make_record(status="running", stage="publish_completed")
    store._write_atomic(rec.to_dict(), store._journal_path(rec.operation_id))
    with pytest.raises(JournalValidationError, match="final_verification_completed"):
        store.update(rec.operation_id, expected_revision=0,
                      status="committed", stage="receipt_persisted",
                      writes_performed=True, document_id=3, chunk_count=100,
                      completion_receipt={"doc": 3})


def test_final_verification_completed_can_committed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    rec = _make_record(status="running", stage="final_verification_completed")
    store._write_atomic(rec.to_dict(), store._journal_path(rec.operation_id))
    updated = store.update(
        rec.operation_id, expected_revision=0,
        status="committed", stage="receipt_persisted",
        writes_performed=True, document_id=3, chunk_count=100,
        completion_receipt={"document_id": 3, "chunk_count": 100},
    )
    assert updated.status == "committed"


# ===================================================================
# strict payload constraints
# ===================================================================


def test_failed_without_completion_receipt_rejected() -> None:
    with pytest.raises(JournalValidationError, match="completion_receipt"):
        _make_record(status="failed", stage="receipt_persisted",
                      error={"code": "e"})


def test_accepted_carrying_error_rejected() -> None:
    with pytest.raises(JournalValidationError, match="must not carry an error"):
        _make_record(status="accepted", error={"code": "e"})


def test_accepted_carrying_completion_receipt_rejected() -> None:
    with pytest.raises(JournalValidationError, match="must not carry a completion_receipt"):
        _make_record(status="accepted", completion_receipt={"d": 1})


def test_running_carrying_error_rejected() -> None:
    with pytest.raises(JournalValidationError, match="must not carry an error"):
        _make_record(status="running", stage="body_import_started",
                      error={"code": "e"})


def test_running_carrying_completion_receipt_rejected() -> None:
    with pytest.raises(JournalValidationError, match="must not carry a completion_receipt"):
        _make_record(status="running", stage="body_import_started",
                      completion_receipt={"d": 1})


def test_committed_carrying_error_rejected() -> None:
    with pytest.raises(JournalValidationError, match="must not carry an error"):
        _make_record(
            status="committed", stage="receipt_persisted",
            writes_performed=True, document_id=1, chunk_count=1,
            completion_receipt={"d": 1}, error={"code": "e"},
        )


def test_orphaned_without_error_rejected() -> None:
    with pytest.raises(JournalValidationError, match="requires a non-null error"):
        _make_record(status="orphaned", stage="body_import_started")


def test_orphaned_carrying_completion_receipt_rejected() -> None:
    with pytest.raises(JournalValidationError, match="must not carry a completion_receipt"):
        _make_record(status="orphaned", stage="body_import_started",
                      error={"code": "e"}, completion_receipt={"d": 1})


def test_orphaned_receipt_persisted_rejected() -> None:
    with pytest.raises(JournalValidationError, match="must not have stage"):
        _make_record(status="orphaned", stage="receipt_persisted",
                      error={"code": "e"})


def test_accepted_direct_failed_receipt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    updated = store.update(
        rec.operation_id, expected_revision=0,
        status="failed", stage="receipt_persisted",
        error={"error_code": "init_failed"},
        completion_receipt={"status": "failed", "reason": "init error"},
    )
    assert updated.status == "failed"
    assert updated.stage == "receipt_persisted"
    assert updated.revision == 1


# ===================================================================
# terminal status — no further updates
# ===================================================================


def test_committed_rejects_any_update(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    rec = _make_record(
        status="committed", stage="receipt_persisted",
        writes_performed=True, document_id=3, chunk_count=100,
        completion_receipt={"doc": 3},
    )
    store._write_atomic(rec.to_dict(), store._journal_path(rec.operation_id))
    with pytest.raises(JournalConflictError, match="terminal"):
        store.update(rec.operation_id, expected_revision=0,
                      heartbeat_at=_ts(12))


def test_failed_rejects_any_update(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    rec = _make_record(
        status="failed", stage="receipt_persisted",
        error={"code": "test"},
        completion_receipt={"status": "failed"},
    )
    store._write_atomic(rec.to_dict(), store._journal_path(rec.operation_id))
    with pytest.raises(JournalConflictError, match="terminal"):
        store.update(rec.operation_id, expected_revision=0,
                      heartbeat_at=_ts(12))


def test_orphaned_rejects_any_update(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    rec = _make_record(
        status="orphaned", stage="body_import_started",
        error={"code": "test"},
    )
    store._write_atomic(rec.to_dict(), store._journal_path(rec.operation_id))
    with pytest.raises(JournalConflictError, match="terminal"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running")


# ===================================================================
# stage transition rules
# ===================================================================


def test_rollback_started_then_publish_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    rec = _make_record(status="running", stage="body_import_completed")
    store._write_atomic(rec.to_dict(), store._journal_path(rec.operation_id))
    store.update(rec.operation_id, expected_revision=0, stage="rollback_started")
    with pytest.raises(JournalValidationError):
        store.update(rec.operation_id, expected_revision=1,
                      status="running", stage="publish_started")


def test_rollback_completed_only_receipt_persisted_allowed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    rec = _make_record(status="running", stage="body_import_completed")
    store._write_atomic(rec.to_dict(), store._journal_path(rec.operation_id))
    store.update(rec.operation_id, expected_revision=0, stage="rollback_started")
    store.update(rec.operation_id, expected_revision=1, stage="rollback_completed")
    with pytest.raises(JournalValidationError):
        store.update(rec.operation_id, expected_revision=2,
                      status="running", stage="publish_started")
    u3 = store.update(rec.operation_id, expected_revision=2,
                       stage="receipt_persisted", status="failed",
                       error={"code": "test"},
                       completion_receipt={"status": "failed"})
    assert u3.stage == "receipt_persisted"


# ===================================================================
# fail-closed scanning
# ===================================================================


def test_corrupt_journal_causes_find_to_raise(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    digest = "e" * 64
    bad_path = store._journal_dir / f"{'b' * 32}.json"
    bad_path.write_text(
        json.dumps(
            {
                "operation_id": "c" * 32,
                "confirmation_token_digest": digest,
                "source_pdf_sha256": _VALID_SHA,
                "transaction_fingerprint": "x",
                "source_revision_fingerprint": "y",
                "started_at": _ts(10),
                "updated_at": _ts(10),
                "revision": 0,
                "status": "accepted",
                "stage": "confirmation_accepted",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(JournalValidationError, match="operation_id"):
        store.find_by_token_digest(digest)


def test_corrupt_journal_causes_resolve_to_raise_not_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    digest = "f" * 64
    bad_path = store._journal_dir / f"{'d' * 32}.json"
    bad_path.write_text(
        json.dumps(
            {
                "operation_id": "d" * 32,
                "confirmation_token_digest": digest,
                "source_pdf_sha256": _VALID_SHA,
                "transaction_fingerprint": "x",
                "source_revision_fingerprint": "y",
                "schema_version": "future.v99",
                "started_at": _ts(10),
                "updated_at": _ts(10),
                "revision": 0,
                "status": "accepted",
                "stage": "confirmation_accepted",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(JournalValidationError):
        store.resolve_by_token_digest(digest)


def test_find_rejects_symlink(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    digest = "e" * 64
    rec = _make_record(confirmation_token_digest=digest)
    store.create(rec)
    op_id2 = "f" * 32
    path2 = store._journal_dir / f"{op_id2}.json"
    path2.write_text(
        json.dumps(
            {
                "operation_id": op_id2,
                "confirmation_token_digest": digest,
                "source_pdf_sha256": _VALID_SHA,
                "transaction_fingerprint": "x",
                "source_revision_fingerprint": "y",
                "started_at": _ts(10),
                "updated_at": _ts(10),
                "revision": 0,
                "status": "accepted",
                "stage": "confirmation_accepted",
            }
        ),
        encoding="utf-8",
    )
    original = Path.is_symlink

    def _fake_symlink(self):  # type: ignore[no-untyped-def]
        if self == path2:
            return True
        return original(self)

    monkeypatch.setattr(Path, "is_symlink", _fake_symlink)
    with pytest.raises(JournalValidationError, match="symlink"):
        store.find_by_token_digest(digest)


def test_matching_filename_directory_causes_fail(tmp_path: Path) -> None:
    """A directory whose name matches the journal pattern must cause
    fail-closed scan abort."""
    store = _store(tmp_path)
    store._ensure_dir()
    dir_path = store._journal_dir / f"{'a' * 32}.json"
    dir_path.mkdir()
    with pytest.raises(JournalValidationError, match="not a regular file"):
        store.find_by_token_digest(_DIGEST_U)


# ===================================================================
# update symlink / filename mismatch
# ===================================================================


def test_update_rejects_symlink(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    path = store._journal_dir / f"{rec.operation_id}.json"
    original = Path.is_symlink

    def _fake_symlink(self):  # type: ignore[no-untyped-def]
        if self == path:
            return True
        return original(self)

    monkeypatch.setattr(Path, "is_symlink", _fake_symlink)
    with pytest.raises(JournalValidationError, match="symlink"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="body_import_started")


def test_update_rejects_filename_op_id_mismatch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    path = store._journal_dir / f"{rec.operation_id}.json"
    path.write_text(
        json.dumps(
            {
                "operation_id": "e" * 32,
                "confirmation_token_digest": _VALID_SHA,
                "source_pdf_sha256": _VALID_SHA,
                "transaction_fingerprint": "x",
                "source_revision_fingerprint": "y",
                "started_at": _ts(10),
                "updated_at": _ts(10),
                "revision": 0,
                "status": "accepted",
                "stage": "confirmation_accepted",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(JournalValidationError, match="operation_id"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="body_import_started")


# ===================================================================
# strict JSON types
# ===================================================================


def test_nested_set_rejected() -> None:
    with pytest.raises(JournalValidationError, match="set is not allowed"):
        _make_record(
            status="failed", stage="receipt_persisted",
            error={"items": {1, 2, 3}},
            completion_receipt={"status": "failed"},
        )


def test_non_string_dict_key_rejected() -> None:
    with pytest.raises(JournalValidationError, match="Non-string dict key"):
        _make_record(
            status="failed", stage="receipt_persisted",
            error={1: "value"},
            completion_receipt={"status": "failed"},
        )


# ===================================================================
# atomic write
# ===================================================================


def test_atomic_write_short_writes_complete(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    real_write = os.write
    chunk_size = [1]

    def _short_write(fd, data):  # type: ignore[no-untyped-def]
        b = data if isinstance(data, bytes) else bytes(data)
        n = min(chunk_size[0], len(b))
        return real_write(fd, b[:n])

    monkeypatch.setattr(os, "write", _short_write)
    rec = _make_record(title="S" * 100)
    store.create(rec)
    path = store._journal_dir / f"{rec.operation_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["title"] == "S" * 100


def test_write_zero_raises_and_leaves_no_final_file(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    call_count = [0]
    real_write = os.write

    def _zero_write(fd, data):  # type: ignore[no-untyped-def]
        call_count[0] += 1
        if call_count[0] == 1:
            return 0
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", _zero_write)
    rec = _make_record()
    with pytest.raises(JournalWriteError):
        store.create(rec)
    path = store._journal_dir / f"{rec.operation_id}.json"
    assert not path.exists()
    for entry in store._journal_dir.iterdir():
        if entry.is_file():
            assert not entry.name.startswith(".")


# ===================================================================
# remaining existing tests
# ===================================================================


def test_confirmation_token_not_stored(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    path = store._journal_dir / f"{rec.operation_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "confirmation_token" not in data
    assert data["confirmation_token_digest"] == _VALID_SHA


def test_update_refreshes_updated_at(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    original = store.read(rec.operation_id)
    assert original is not None
    before = original.updated_at
    updated = store.update(rec.operation_id, expected_revision=0,
                            status="running", stage="body_import_started")
    assert updated.updated_at > before


def test_expected_status_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record(status="accepted")
    store.create(rec)
    with pytest.raises(JournalConflictError, match="Expected status"):
        store.update(rec.operation_id, expected_revision=0,
                      expected_status="running",
                      status="running", stage="body_import_started")


def test_expected_status_passes_when_matching(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record(status="accepted")
    store.create(rec)
    updated = store.update(rec.operation_id, expected_revision=0,
                            expected_status="accepted",
                            status="running", stage="body_import_started")
    assert updated.status == "running"


def test_invalid_status_rejected_on_create() -> None:
    with pytest.raises(JournalValidationError, match="Invalid status"):
        _make_record(status="bogus")


def test_invalid_status_rejected_on_update(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalValidationError, match="cannot transition"):
        store.update(rec.operation_id, expected_revision=0,
                      status="bogus")


def test_invalid_stage_rejected_on_create() -> None:
    with pytest.raises(JournalValidationError, match="Invalid stage"):
        _make_record(stage="unknown_stage")


def test_invalid_stage_rejected_on_update(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalValidationError, match="Invalid stage"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="unknown_stage")


def test_stage_cannot_regress(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    rec = _make_record(status="running", stage="body_import_completed")
    store._write_atomic(rec.to_dict(), store._journal_path(rec.operation_id))
    with pytest.raises(JournalValidationError, match="cannot regress"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="body_import_started")


def test_stage_monotonic_advance_ok(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    u1 = store.update(rec.operation_id, expected_revision=0,
                       status="running", stage="body_import_started")
    assert u1.stage == "body_import_started"
    u2 = store.update(rec.operation_id, expected_revision=1,
                       status="running", stage="body_import_completed")
    assert u2.stage == "body_import_completed"


def test_same_stage_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    rec = _make_record(status="running", stage="body_import_started")
    store._write_atomic(rec.to_dict(), store._journal_path(rec.operation_id))
    updated = store.update(rec.operation_id, expected_revision=0,
                            stage="body_import_started",
                            heartbeat_at=_ts(11))
    assert updated.stage == "body_import_started"


def test_find_by_token_digest_returns_empty_for_unknown(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    assert store.find_by_token_digest(_DIGEST_U) == []


def test_multiple_active_journals_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    digest = _DIGEST_M
    store._ensure_dir()
    r1 = _make_record(confirmation_token_digest=digest, status="accepted",
                        updated_at=_ts(10), operation_id="a" * 32)
    r2 = _make_record(confirmation_token_digest=digest, status="running",
                        stage="body_import_started", updated_at=_ts(11),
                        operation_id="b" * 32)
    store._write_atomic(r1.to_dict(), store._journal_path("a" * 32))
    store._write_atomic(r2.to_dict(), store._journal_path("b" * 32))
    with pytest.raises(JournalConflictError, match="Multiple active operations"):
        store.find_latest_by_token_digest(digest)


def test_committed_and_failed_coexist_ok(tmp_path: Path) -> None:
    store = _store(tmp_path)
    digest = _DIGEST_N
    store._ensure_dir()
    r1 = _make_record(
        confirmation_token_digest=digest, status="committed",
        updated_at=_ts(10), stage="receipt_persisted",
        writes_performed=True, document_id=1, chunk_count=1,
        completion_receipt={"doc": 1}, operation_id="a" * 32,
    )
    r2 = _make_record(
        confirmation_token_digest=digest, status="failed",
        updated_at=_ts(11), stage="receipt_persisted",
        error={"code": "test"}, completion_receipt={"status": "f"},
        operation_id="b" * 32,
    )
    store._write_atomic(r1.to_dict(), store._journal_path("a" * 32))
    store._write_atomic(r2.to_dict(), store._journal_path("b" * 32))
    result = store.find_latest_by_token_digest(digest)
    assert result is not None
    assert result.status == "failed"


def test_single_active_returns_correctly(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ensure_dir()
    digest = _DIGEST_P
    r1 = _make_record(confirmation_token_digest=digest, status="running",
                        stage="body_import_started", updated_at=_ts(10))
    store._write_atomic(r1.to_dict(), store._journal_path(r1.operation_id))
    result = store.find_latest_by_token_digest(digest)
    assert result is not None
    assert result.status == "running"


def test_find_latest_empty_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.find_latest_by_token_digest(_DIGEST_X) is None


def test_atomic_replace_failure_preserves_old_journal(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    rec = _make_record(title="original")
    store.create(rec)
    original_loaded = store.read(rec.operation_id)
    assert original_loaded is not None
    assert original_loaded.title == "original"
    real_replace = os.replace

    def _failing_replace(src, dst, **__kwargs):  # type: ignore[no-untyped-def]
        dst_path = Path(str(dst))
        if dst_path.name.endswith(".json") and "operation_journal" in str(dst):
            raise OSError("simulated replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _failing_replace)
    with pytest.raises(JournalWriteError, match="Atomic replace failed"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="body_import_started")
    reloaded = store.read(rec.operation_id)
    assert reloaded is not None
    assert reloaded.title == "original"
    assert reloaded.stage == "confirmation_accepted"
    assert reloaded.revision == 0


def test_temp_file_cleaned_after_replace_failure(
    tmp_path, monkeypatch
) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    real_replace = os.replace

    def _failing_replace(src, dst, **__kwargs):  # type: ignore[no-untyped-def]
        dst_path = Path(str(dst))
        if dst_path.name.endswith(".json") and "operation_journal" in str(dst):
            raise OSError("simulated replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _failing_replace)
    with pytest.raises(JournalWriteError):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="body_import_started")
    journal_dir = store._journal_dir
    for entry in journal_dir.iterdir():
        if entry.is_file():
            assert not entry.name.startswith(".")
            assert entry.suffix == ".json"
            assert entry.name == f"{rec.operation_id}.json"


def test_json_readable_by_stdlib(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record(title="Algorithm")
    store.create(rec)
    path = store._journal_dir / f"{rec.operation_id}.json"
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["title"] == "Algorithm"


def test_wrong_schema_version_rejected() -> None:
    with pytest.raises(JournalValidationError, match="schema_version"):
        _make_record(schema_version="wrong.version")


def test_empty_operation_id_rejected() -> None:
    rec_args = dict(
        confirmation_token_digest=_VALID_SHA,
        source_pdf_sha256=_VALID_SHA,
        transaction_fingerprint="x",
        source_revision_fingerprint="y",
    )
    with pytest.raises(JournalValidationError, match="operation_id"):
        ImportOperationJournal(operation_id="", **rec_args)  # type: ignore[arg-type]


def test_empty_confirmation_token_digest_rejected() -> None:
    with pytest.raises(JournalValidationError, match="confirmation_token_digest"):
        _make_record(confirmation_token_digest="")


def test_heartbeat_at_not_auto_refreshed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record(heartbeat_at="2026-07-31T10:00:00+00:00")
    store.create(rec)
    loaded = store.read(rec.operation_id)
    assert loaded is not None
    original_heartbeat = loaded.heartbeat_at
    updated = store.update(rec.operation_id, expected_revision=0,
                            status="running", stage="body_import_started")
    assert updated.heartbeat_at == original_heartbeat
    new_heartbeat = "2026-07-31T11:00:00+00:00"
    refreshed = store.update(rec.operation_id, expected_revision=1,
                              heartbeat_at=new_heartbeat)
    assert refreshed.heartbeat_at == new_heartbeat


def test_update_rejects_started_at_modification(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalValidationError, match="started_at must not"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="body_import_started",
                      started_at=_ts(12))


def test_update_rejects_operation_type_modification(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record()
    store.create(rec)
    with pytest.raises(JournalValidationError, match="operation_type must not"):
        store.update(rec.operation_id, expected_revision=0,
                      status="running", stage="body_import_started",
                      operation_type="delete_document")


def test_from_dict_rejects_unknown_fields() -> None:
    with pytest.raises(JournalValidationError, match="Unknown fields"):
        ImportOperationJournal.from_dict(
            {
                "operation_id": _VALID_OP_ID,
                "confirmation_token_digest": _VALID_SHA,
                "source_pdf_sha256": _VALID_SHA,
                "transaction_fingerprint": "x",
                "source_revision_fingerprint": "y",
                "started_at": _ts(10),
                "updated_at": _ts(10),
                "revision": 0,
                "status": "accepted",
                "stage": "confirmation_accepted",
                "secret_token": "should_not_be_here",
            }
        )


def test_create_rejects_wrong_type(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(TypeError, match="ImportOperationJournal"):
        store.create({"operation_id": "x"})  # type: ignore[arg-type]


def test_two_accepted_are_active(tmp_path: Path) -> None:
    store = _store(tmp_path)
    digest = _DIGEST_Q
    store._ensure_dir()
    r1 = _make_record(confirmation_token_digest=digest, status="accepted",
                        operation_id="a" * 32)
    r2 = _make_record(confirmation_token_digest=digest, status="accepted",
                        operation_id="b" * 32)
    store._write_atomic(r1.to_dict(), store._journal_path("a" * 32))
    store._write_atomic(r2.to_dict(), store._journal_path("b" * 32))
    with pytest.raises(JournalConflictError, match="Multiple active operations"):
        store.find_latest_by_token_digest(digest)


def test_update_nonexistent_operation_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(JournalConflictError, match="not found"):
        store.update(_VALID_OP_ID, expected_revision=0,
                      status="running", stage="body_import_started")


def test_revision_is_forced_to_zero_on_create(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = _make_record(revision=0)
    created = store.create(rec)
    assert created.revision == 0
