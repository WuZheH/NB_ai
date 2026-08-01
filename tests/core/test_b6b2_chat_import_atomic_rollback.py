import sqlite3
import hashlib
from pathlib import Path
import pytest
from app.services.chat_pdf_production_import_service import ChatPdfImportRuntime, import_document_to_production
from app.services.local_pdf_source_binding_service import (
    LocalPdfSourceBinding,
    record_document_source,
)

def _runtime(tmp_path, body):
    db = tmp_path / "db.sqlite"; data = tmp_path / "data"; data.mkdir()
    with sqlite3.connect(db) as c:
        c.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,title TEXT,document_type TEXT); CREATE TABLE knowledge_chunks(id INTEGER PRIMARY KEY,document_id INTEGER,chunk_index INTEGER); CREATE TABLE document_sources(id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL,source_type TEXT NOT NULL,source_trace_json TEXT NOT NULL,created_at TEXT NOT NULL);")
    return ChatPdfImportRuntime(db, data, tmp_path/"fts.db", tmp_path/"fts.json", tmp_path/"vectors", tmp_path/"vector.json", None, body), db

def _seed_body(db, *, count=1, fail=False, record_source=True):
    def body(_job, _kind, source_binding):
        with sqlite3.connect(db) as c:
            ids=[]
            for i in range(count):
                cur=c.execute("INSERT INTO documents(title,document_type) VALUES('x','paper')",()); ids.append(cur.lastrowid); c.execute("INSERT INTO knowledge_chunks(document_id,chunk_index) VALUES(?,0)",(cur.lastrowid,))
            c.commit()
        if record_source and len(ids) == 1:
            record_document_source(
                db_path=db,
                document_id=int(ids[0]),
                binding=source_binding,
            )
        if fail: raise RuntimeError("body_failed_after_write")
        return {"document_id": ids[0], "title":"x", "chunk_count":1}
    return body


def _binding(tmp_path: Path) -> LocalPdfSourceBinding:
    relative = "pdfs/chat_imports/fixture.pdf"
    managed = tmp_path / "data" / Path(relative)
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_bytes(b"%PDF-1.4 fixture")
    digest = hashlib.sha256(managed.read_bytes()).hexdigest()
    revision = "b" * 64
    return LocalPdfSourceBinding(
        source_identity=f"local_pdf:sha256:{digest}",
        pdf_sha256=digest,
        source_revision_fingerprint=revision,
        managed_pdf_relative_path=relative,
        import_history={
            "previewed_at": "2026-08-01T00:00:00+00:00",
            "confirmed_at": "2026-08-01T00:01:00+00:00",
            "transaction_fingerprint": "c" * 64,
            "confirmation_token_fingerprint": "d" * 64,
            "source_revision_fingerprint": revision,
            "lifecycle_events": [
                "previewed",
                "confirmed",
                "transaction_started",
            ],
        },
    )

from app.services.chat_pdf_production_import_service import import_document_to_production


def test_production_orchestrator_requires_explicit_opt_in():
    with pytest.raises(RuntimeError, match="opt_in"):
        import_document_to_production(import_job_id="missing", document_type="paper")

@pytest.fixture
def seams(monkeypatch):
    monkeypatch.setattr("app.services.chat_pdf_production_import_service._fts_status", lambda _r: {"status":"ready","ready":True})
    monkeypatch.setattr("app.services.chat_pdf_production_import_service.chat_local_note_import_service.import_local_notes", lambda **_: {"note_count":0,"evidence_link_count":0})
    monkeypatch.setattr("app.services.chat_pdf_production_import_service.fts_index_service.upsert_document_retrieval_fts", lambda **_: {"status":"ready"})
    monkeypatch.setattr("app.services.chat_pdf_production_import_service.vector_store_service.sync_affected_passage_embeddings", lambda *a,**k: {"scope":"affected_source_ids_only","full_rebuild_allowed":False,"delete_orphans_allowed":False,"upserted_count":1})

def _rollback(db, calls):
    def rb(doc, runtime):
        calls.append(doc)
        with sqlite3.connect(db) as c:
            c.execute("DELETE FROM document_sources WHERE document_id=?",(doc,))
            c.execute("DELETE FROM knowledge_chunks WHERE document_id=?",(doc,)); c.execute("DELETE FROM documents WHERE id=?",(doc,)); c.commit()
        return {"status":"completed"}
    return rb

def test_temp_orchestrator_success_and_contract(tmp_path, seams, monkeypatch):
    runtime, db = _runtime(tmp_path, _seed_body(tmp_path/"db.sqlite"))
    calls=[]; monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", _rollback(db,calls))
    result=import_document_to_production(import_job_id="x", document_type="paper", source_binding=_binding(tmp_path), allow_production=False, runtime=runtime)
    assert result["status"] == "completed" and not calls
    assert result["source_binding_count"] == 1
    assert result["source_type"] == "local_pdf"
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM document_sources"
        ).fetchone()[0] == 1

@pytest.mark.parametrize("failure", ["notes", "fts", "vector"])
def test_temp_orchestrator_post_body_failures_rollback(tmp_path, seams, monkeypatch, failure):
    runtime, db = _runtime(tmp_path, _seed_body(tmp_path/"db.sqlite")); calls=[]
    monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", _rollback(db,calls))
    if failure == "notes": monkeypatch.setattr("app.services.chat_pdf_production_import_service.chat_local_note_import_service.import_local_notes", lambda **_: (_ for _ in ()).throw(RuntimeError("notes")))
    elif failure == "fts": monkeypatch.setattr("app.services.chat_pdf_production_import_service.fts_index_service.upsert_document_retrieval_fts", lambda **_: (_ for _ in ()).throw(RuntimeError("fts")))
    elif failure == "vector": monkeypatch.setattr("app.services.chat_pdf_production_import_service.vector_store_service.sync_affected_passage_embeddings", lambda *a,**k: (_ for _ in ()).throw(RuntimeError("vector")))
    with pytest.raises(RuntimeError): import_document_to_production(import_job_id="x", document_type="paper", source_binding=_binding(tmp_path), runtime=runtime)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] == 0
    assert len(calls) == 1

def test_bad_vector_final_contract_rolls_back(tmp_path, seams, monkeypatch):
    runtime, db = _runtime(tmp_path, _seed_body(tmp_path/"db.sqlite")); calls=[]; monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", _rollback(db,calls))
    monkeypatch.setattr("app.services.chat_pdf_production_import_service.vector_store_service.sync_affected_passage_embeddings", lambda *a,**k: {"scope":"wrong_scope","full_rebuild_allowed":False,"delete_orphans_allowed":False,"upserted_count":0})
    with pytest.raises(RuntimeError, match="chat_import_final_verify_failed"): import_document_to_production(import_job_id="x", document_type="paper", source_binding=_binding(tmp_path), runtime=runtime)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] == 0
    assert len(calls)==1

def test_final_fts_status_failure_after_body_rolls_back(tmp_path, seams, monkeypatch):
    runtime, db = _runtime(tmp_path, _seed_body(tmp_path/"db.sqlite")); calls=[]; monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", _rollback(db,calls))
    states=iter(({"status":"ready","ready":True},{"status":"broken","ready":False}))
    monkeypatch.setattr("app.services.chat_pdf_production_import_service._fts_status", lambda _r: next(states))
    with pytest.raises(RuntimeError, match="chat_import_final_verify_failed"): import_document_to_production(import_job_id="x", document_type="paper", source_binding=_binding(tmp_path), runtime=runtime)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] == 0
    assert len(calls)==1

def test_body_write_then_raise_rolls_back(tmp_path, seams, monkeypatch):
    runtime, db = _runtime(tmp_path, _seed_body(tmp_path/"db.sqlite", fail=True)); calls=[]; monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", _rollback(db,calls))
    with pytest.raises(RuntimeError, match="body_failed_after_write"): import_document_to_production(import_job_id="x", document_type="paper", source_binding=_binding(tmp_path), runtime=runtime)
    assert len(calls)==1

def test_body_before_write_has_no_rollback(tmp_path, seams, monkeypatch):
    runtime, db = _runtime(tmp_path, lambda *_: (_ for _ in ()).throw(RuntimeError("body"))); calls=[]; monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", _rollback(db,calls))
    with pytest.raises(RuntimeError): import_document_to_production(import_job_id="x", document_type="paper", source_binding=_binding(tmp_path), runtime=runtime)
    assert calls == []

def test_ambiguous_delta_fails_closed(tmp_path, seams, monkeypatch):
    runtime, db = _runtime(tmp_path, _seed_body(tmp_path/"db.sqlite", count=2, fail=True)); calls=[]; monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", _rollback(db,calls))
    with pytest.raises(RuntimeError, match="ambiguous"): import_document_to_production(import_job_id="x", document_type="paper", source_binding=_binding(tmp_path), runtime=runtime)
    assert calls == []

def test_rollback_failure_is_hard_error(tmp_path, seams, monkeypatch):
    runtime, _ = _runtime(tmp_path, _seed_body(tmp_path/"db.sqlite")); monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", lambda *a: (_ for _ in ()).throw(RuntimeError("fail")))
    monkeypatch.setattr("app.services.chat_pdf_production_import_service.chat_local_note_import_service.import_local_notes", lambda **_: (_ for _ in ()).throw(RuntimeError("notes")))
    with pytest.raises(RuntimeError, match="chat_import_rollback_failed"): import_document_to_production(import_job_id="x", document_type="paper", source_binding=_binding(tmp_path), runtime=runtime)


def test_source_recorder_failure_rolls_back_document_and_chunks(
    tmp_path,
    seams,
    monkeypatch,
):
    runtime, db = _runtime(
        tmp_path,
        _seed_body(tmp_path / "db.sqlite", record_source=False),
    )
    calls = []
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service._rollback_document",
        _rollback(db, calls),
    )

    def recorder_failure(_job, _kind, _binding):
        result = _seed_body(db, record_source=False)(
            _job,
            _kind,
            _binding,
        )
        assert result["document_id"] > 0
        raise RuntimeError("local_pdf_document_source_write_failed")

    runtime = ChatPdfImportRuntime(
        runtime.db_path,
        runtime.data_dir,
        runtime.fts_path,
        runtime.fts_manifest_path,
        runtime.vector_store_path,
        runtime.vector_manifest_path,
        runtime.deletion_runtime,
        recorder_failure,
    )
    with pytest.raises(
        RuntimeError,
        match="local_pdf_document_source_write_failed",
    ):
        import_document_to_production(
            import_job_id="x",
            document_type="paper",
            source_binding=_binding(tmp_path),
            runtime=runtime,
        )
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_chunks"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM document_sources"
        ).fetchone()[0] == 0
    assert len(calls) == 1


def test_final_verification_requires_local_pdf_source_binding(
    tmp_path,
    seams,
    monkeypatch,
):
    runtime, db = _runtime(
        tmp_path,
        _seed_body(tmp_path / "db.sqlite", record_source=False),
    )
    calls = []
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service._rollback_document",
        _rollback(db, calls),
    )
    with pytest.raises(
        RuntimeError,
        match="local_pdf_document_source_count_invalid",
    ):
        import_document_to_production(
            import_job_id="x",
            document_type="paper",
            source_binding=_binding(tmp_path),
            runtime=runtime,
        )
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0
    assert len(calls) == 1


def test_final_verification_rejects_source_trace_sha_mismatch(
    tmp_path,
    seams,
    monkeypatch,
):
    def wrong_source_body(_job, _kind, binding):
        result = _seed_body(tmp_path / "db.sqlite")(
            _job,
            _kind,
            binding,
        )
        with sqlite3.connect(tmp_path / "db.sqlite") as connection:
            row = connection.execute(
                "SELECT id, source_trace_json FROM document_sources"
            ).fetchone()
            import json

            trace = json.loads(row[1])
            trace["source_pdf_sha256"] = "0" * 64
            connection.execute(
                "UPDATE document_sources SET source_trace_json=? WHERE id=?",
                (json.dumps(trace, sort_keys=True), row[0]),
            )
            connection.commit()
        return result

    runtime, db = _runtime(tmp_path, wrong_source_body)
    calls = []
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service._rollback_document",
        _rollback(db, calls),
    )
    with pytest.raises(
        RuntimeError,
        match="local_pdf_document_source_trace_mismatch",
    ):
        import_document_to_production(
            import_job_id="x",
            document_type="paper",
            source_binding=_binding(tmp_path),
            runtime=runtime,
        )
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM document_sources"
        ).fetchone()[0] == 0
    assert len(calls) == 1


def test_source_sha_mismatch_stops_before_body_mutation(
    tmp_path,
    seams,
):
    calls = []

    def body(*_args):
        calls.append("body")
        raise AssertionError("body must not run")

    runtime, db = _runtime(tmp_path, body)
    binding = _binding(tmp_path)
    managed = tmp_path / "data" / binding.managed_pdf_relative_path
    managed.write_bytes(b"%PDF-1.4 changed")
    with pytest.raises(
        RuntimeError,
        match="local_pdf_managed_pdf_sha_mismatch",
    ):
        import_document_to_production(
            import_job_id="x",
            document_type="paper",
            source_binding=binding,
            runtime=runtime,
        )
    assert calls == []
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("after_source_binding", [False, True])
def test_base_exception_before_or_after_source_binding_rolls_back(
    tmp_path,
    seams,
    monkeypatch,
    after_source_binding,
):
    runtime, db = _runtime(tmp_path, lambda *_: {})
    calls = []
    monkeypatch.setattr(
        "app.services.chat_pdf_production_import_service._rollback_document",
        _rollback(db, calls),
    )

    def body(_job, _kind, binding):
        if after_source_binding:
            _seed_body(db)(_job, _kind, binding)
        raise KeyboardInterrupt("owner aborted")

    runtime = ChatPdfImportRuntime(
        runtime.db_path,
        runtime.data_dir,
        runtime.fts_path,
        runtime.fts_manifest_path,
        runtime.vector_store_path,
        runtime.vector_manifest_path,
        runtime.deletion_runtime,
        body,
    )
    with pytest.raises(KeyboardInterrupt, match="owner aborted"):
        import_document_to_production(
            import_job_id="x",
            document_type="paper",
            source_binding=_binding(tmp_path),
            runtime=runtime,
        )
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM document_sources"
        ).fetchone()[0] == 0
    assert len(calls) == (1 if after_source_binding else 0)

@pytest.mark.parametrize("document_type", ["paper", "book", "report"])
def test_production_orchestrator_requires_opt_in_for_all_document_types(document_type: str):
    with pytest.raises(RuntimeError, match="opt_in"):
        import_document_to_production(import_job_id="missing", document_type=document_type)
