from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.services import vector_store_service as service


@dataclass(frozen=True)
class _Field:
    name: str


class _Query:
    def __init__(self, table: "_Table") -> None:
        self.table = table
        self.where_clause = ""
        self.limit_value = 0

    def where(self, clause: str) -> "_Query":
        self.where_clause = clause
        return self

    def limit(self, value: int) -> "_Query":
        self.limit_value = value
        return self

    def to_list(self) -> list[dict[str, Any]]:
        self.table.queries.append((self.where_clause, self.limit_value))
        return self.table.filtered(self.where_clause)[: self.limit_value]


class _Table:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        fields: tuple[str, ...] = ("source_id", "document_id"),
        fail_query: bool = False,
    ) -> None:
        self.rows = rows
        self.schema = [_Field(name) for name in fields]
        self.fail_query = fail_query
        self.queries: list[tuple[str, int]] = []

    def filtered(self, clause: str) -> list[dict[str, Any]]:
        if self.fail_query:
            raise RuntimeError("query failed")
        if clause.startswith("document_id = "):
            value = int(clause.split("=")[-1].strip())
            return [row for row in self.rows if row.get("document_id") == value]
        if clause.startswith("source_id IN ("):
            return [
                row
                for row in self.rows
                if str(row.get("source_id")) in clause.replace("''", "'")
            ]
        raise AssertionError(f"unexpected clause: {clause}")

    def count_rows(self, clause: str) -> int:
        return len(self.filtered(clause))

    def search(self) -> _Query:
        return _Query(self)


class _Db:
    def __init__(self, tables: dict[str, _Table]) -> None:
        self.tables = tables

    def table_names(self) -> list[str]:
        return list(self.tables)

    def open_table(self, name: str) -> _Table:
        return self.tables[name]


def _run_with_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    db: object,
) -> dict[str, Any]:
    store = tmp_path / "vectors"
    store.mkdir()
    monkeypatch.setattr(
        service,
        "_connect_existing_vector_store",
        lambda _path: db,
    )
    return service.inspect_document_vector_state(
        document_id=1,
        expected_passage_source_ids=["chunk:1:1"],
        expected_note_source_ids=["note:1"],
        store_path=store,
    )


def _inspect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    passage_rows: list[dict[str, Any]],
    note_rows: list[dict[str, Any]],
    passage_fields: tuple[str, ...] = ("source_id", "document_id"),
    note_fields: tuple[str, ...] = ("source_id", "document_id"),
) -> tuple[dict[str, Any], _Db, Path]:
    store = tmp_path / "vectors"
    store.mkdir()
    db = _Db(
        {
            service.PASSAGE_TABLE: _Table(
                passage_rows,
                fields=passage_fields,
            ),
            service.NOTE_TABLE: _Table(note_rows, fields=note_fields),
        }
    )
    monkeypatch.setattr(
        service,
        "_connect_existing_vector_store",
        lambda _path: db,
    )
    before = sorted(path.name for path in store.iterdir())
    result = service.inspect_document_vector_state(
        document_id=1,
        expected_passage_source_ids=["chunk:1:1", "chunk:1:2"],
        expected_note_source_ids=["note:1", "note:'quoted'"],
        store_path=store,
    )
    assert sorted(path.name for path in store.iterdir()) == before
    return result, db, store


def test_document_vector_state_no_orphan_and_quoted_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, db, _store = _inspect(
        tmp_path,
        monkeypatch,
        passage_rows=[
            {"document_id": 1, "source_id": "chunk:1:1"},
            {"document_id": 1, "source_id": "chunk:1:2"},
            {"document_id": 2, "source_id": "chunk:2:1"},
        ],
        note_rows=[
            {"document_id": 1, "source_id": "note:1"},
            {"document_id": 1, "source_id": "note:'quoted'"},
        ],
    )
    assert result["status"] == "ok"
    assert result["passage"]["missing_count"] == 0
    assert result["passage"]["orphan_count"] == 0
    assert result["note"]["missing_count"] == 0
    assert result["note"]["orphan_count"] == 0
    assert db.tables[service.PASSAGE_TABLE].queries == [("document_id = 1", 2)]


def test_document_vector_state_missing_and_orphans_can_coexist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _db, _store = _inspect(
        tmp_path,
        monkeypatch,
        passage_rows=[
            {"document_id": 1, "source_id": "chunk:1:1"},
            {"document_id": 1, "source_id": "chunk:1:99"},
        ],
        note_rows=[
            {"document_id": 1, "source_id": "note:1"},
            {"document_id": 1, "source_id": "note:orphan"},
            {"document_id": 2, "source_id": "note:foreign"},
        ],
    )
    assert result["passage"]["missing_source_ids"] == ["chunk:1:2"]
    assert result["passage"]["orphan_source_ids"] == ["chunk:1:99"]
    assert result["note"]["missing_source_ids"] == ["note:'quoted'"]
    assert result["note"]["orphan_source_ids"] == ["note:orphan"]


@pytest.mark.parametrize(
    ("rows", "orphan_count"),
    (
        ([], 0),
        ([{"document_id": 1, "source_id": "chunk:1:99"}], 1),
        ([{"document_id": 2, "source_id": "chunk:2:99"}], 0),
    ),
)
def test_zero_passage_expectations_still_find_document_orphans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    orphan_count: int,
) -> None:
    store = tmp_path / "vectors"
    store.mkdir()
    db = _Db(
        {
            service.PASSAGE_TABLE: _Table(rows),
            service.NOTE_TABLE: _Table([]),
        }
    )
    monkeypatch.setattr(
        service,
        "_connect_existing_vector_store",
        lambda _path: db,
    )

    result = service.inspect_document_vector_state(
        document_id=1,
        expected_passage_source_ids=[],
        expected_note_source_ids=[],
        store_path=store,
    )

    assert result["passage"]["expected_source_ids"] == []
    assert result["passage"]["missing_count"] == 0
    assert result["passage"]["orphan_count"] == orphan_count


@pytest.mark.parametrize(
    ("kind", "bad_source_id"),
    (
        ("passage", True),
        ("passage", {"id": "chunk:1:1"}),
        ("passage", ["chunk:1:1"]),
        ("passage", None),
        ("passage", ""),
        ("passage", "bad:1:1"),
        ("passage", "chunk:2:1"),
        ("note", True),
        ("note", {"id": "note:1"}),
        ("note", ["note:1"]),
        ("note", None),
        ("note", ""),
        ("note", "chunk:1:1"),
        ("note", "note:"),
    ),
)
def test_malformed_source_id_rows_degrade_without_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    bad_source_id: object,
) -> None:
    class UnfilteredTable(_Table):
        def filtered(self, clause: str) -> list[dict[str, Any]]:
            if clause.startswith("document_id = "):
                return self.rows
            return super().filtered(clause)

    good_passage = _Table([])
    good_note = _Table([])
    bad_table = UnfilteredTable(
        [{"document_id": 1, "source_id": bad_source_id}]
    )
    db = _Db(
        {
            service.PASSAGE_TABLE: (
                bad_table if kind == "passage" else good_passage
            ),
            service.NOTE_TABLE: bad_table if kind == "note" else good_note,
        }
    )
    result = _run_with_db(tmp_path, monkeypatch, db)

    assert result[kind]["reason"] == f"{kind}_row_parse_failed"


def test_old_schema_reports_capability_unavailable_without_full_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, db, _store = _inspect(
        tmp_path,
        monkeypatch,
        passage_rows=[{"source_id": "chunk:1:1"}],
        note_rows=[{"source_id": "note:1"}],
        passage_fields=("source_id",),
        note_fields=("source_id",),
    )
    assert result["status"] == "capability_unavailable"
    assert result["passage"]["orphan_count"] == "not_available"
    assert result["note"]["orphan_count"] == "not_available"
    assert all(
        query[0].startswith("source_id IN (")
        for table in db.tables.values()
        for query in table.queries
    )


def test_connection_failure_degrades_structurally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "vectors"
    store.mkdir()

    def fail_connect(_path: Path) -> object:
        raise RuntimeError("connect failed")

    monkeypatch.setattr(service, "_connect_existing_vector_store", fail_connect)

    result = service.inspect_document_vector_state(
        document_id=1,
        expected_passage_source_ids=["chunk:1:1"],
        expected_note_source_ids=["note:1"],
        store_path=store,
    )

    assert result["status"] == "unavailable"
    assert result["passage"]["reason"] == "vector_store_connection_failed"


def test_table_list_failure_degrades_structurally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ListFailDb:
        def table_names(self) -> list[str]:
            raise RuntimeError("table list failed")

    result = _run_with_db(tmp_path, monkeypatch, ListFailDb())

    assert result["status"] == "unavailable"
    assert result["passage"]["reason"] == "passage_table_list_failed"
    assert result["note"]["reason"] == "note_table_list_failed"


def test_table_open_failure_degrades_structurally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OpenFailDb:
        def table_names(self) -> list[str]:
            return [service.PASSAGE_TABLE, service.NOTE_TABLE]

        def open_table(self, _name: str) -> object:
            raise RuntimeError("table open failed")

    result = _run_with_db(tmp_path, monkeypatch, OpenFailDb())

    assert result["passage"]["reason"] == "passage_table_open_failed"
    assert result["note"]["reason"] == "note_table_open_failed"


def test_schema_failure_degrades_structurally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SchemaFailTable:
        @property
        def schema(self) -> object:
            raise RuntimeError("schema failed")

    table = SchemaFailTable()
    db = _Db({service.PASSAGE_TABLE: table, service.NOTE_TABLE: table})  # type: ignore[dict-item]
    result = _run_with_db(tmp_path, monkeypatch, db)

    assert result["passage"]["reason"] == "passage_schema_read_failed"
    assert result["note"]["reason"] == "note_schema_read_failed"


def test_query_failure_degrades_structurally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = _Table([], fail_query=True)
    db = _Db({service.PASSAGE_TABLE: table, service.NOTE_TABLE: table})
    result = _run_with_db(tmp_path, monkeypatch, db)

    assert result["passage"]["reason"] == "passage_document_query_failed"
    assert result["note"]["reason"] == "note_document_query_failed"


def test_malformed_document_id_row_degrades_structurally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MalformedTable(_Table):
        def filtered(self, clause: str) -> list[dict[str, Any]]:
            if clause.startswith("document_id = "):
                return self.rows
            return super().filtered(clause)

    passage = MalformedTable(
        [{"document_id": "not-an-int", "source_id": "chunk:1:1"}]
    )
    note = MalformedTable(
        [{"document_id": True, "source_id": "note:1"}]
    )
    db = _Db({service.PASSAGE_TABLE: passage, service.NOTE_TABLE: note})

    result = _run_with_db(tmp_path, monkeypatch, db)

    assert result["status"] == "unavailable"
    assert result["passage"]["reason"] == "passage_row_parse_failed"
    assert result["note"]["reason"] == "note_row_parse_failed"


@pytest.mark.parametrize("invalid", (0, -1, True, False))
def test_document_vector_state_rejects_invalid_document_id(
    tmp_path: Path,
    invalid: object,
) -> None:
    with pytest.raises(ValueError, match="document_id"):
        service.inspect_document_vector_state(
            document_id=invalid,  # type: ignore[arg-type]
            expected_passage_source_ids=[],
            expected_note_source_ids=[],
            store_path=tmp_path,
        )
