import sqlite3
from pathlib import Path
import pytest
from app.services.chat_pdf_production_import_service import ChatPdfImportRuntime, import_document_to_production

def _runtime(tmp_path, body):
    db = tmp_path / "db.sqlite"; data = tmp_path / "data"; data.mkdir()
    with sqlite3.connect(db) as c:
        c.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,title TEXT,document_type TEXT); CREATE TABLE knowledge_chunks(id INTEGER PRIMARY KEY,document_id INTEGER,chunk_index INTEGER);")
    return ChatPdfImportRuntime(db, data, tmp_path/"fts.db", tmp_path/"fts.json", tmp_path/"vectors", tmp_path/"vector.json", None, body), db

def _seed_body(db, *, count=1, fail=False):
    def body(_job, _kind):
        with sqlite3.connect(db) as c:
            ids=[]
            for i in range(count):
                cur=c.execute("INSERT INTO documents(title,document_type) VALUES('x','paper')",()); ids.append(cur.lastrowid); c.execute("INSERT INTO knowledge_chunks(document_id,chunk_index) VALUES(?,0)",(cur.lastrowid,))
            c.commit()
        if fail: raise RuntimeError("body_failed_after_write")
        return {"document_id": ids[0], "title":"x", "chunk_count":1}
    return body

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
            c.execute("DELETE FROM knowledge_chunks WHERE document_id=?",(doc,)); c.execute("DELETE FROM documents WHERE id=?",(doc,)); c.commit()
        return {"status":"completed"}
    return rb

def test_temp_orchestrator_success_and_contract(tmp_path, seams, monkeypatch):
    runtime, db = _runtime(tmp_path, _seed_body(tmp_path/"db.sqlite"))
    calls=[]; monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", _rollback(db,calls))
    result=import_document_to_production(import_job_id="x", document_type="paper", allow_production=False, runtime=runtime)
    assert result["status"] == "completed" and not calls

@pytest.mark.parametrize("failure", ["notes", "fts", "vector", "verify"])
def test_temp_orchestrator_post_body_failures_rollback(tmp_path, seams, monkeypatch, failure):
    runtime, db = _runtime(tmp_path, _seed_body(tmp_path/"db.sqlite")); calls=[]
    monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", _rollback(db,calls))
    if failure == "notes": monkeypatch.setattr("app.services.chat_pdf_production_import_service.chat_local_note_import_service.import_local_notes", lambda **_: (_ for _ in ()).throw(RuntimeError("notes")))
    elif failure == "fts": monkeypatch.setattr("app.services.chat_pdf_production_import_service.fts_index_service.upsert_document_retrieval_fts", lambda **_: (_ for _ in ()).throw(RuntimeError("fts")))
    elif failure == "vector": monkeypatch.setattr("app.services.chat_pdf_production_import_service.vector_store_service.sync_affected_passage_embeddings", lambda *a,**k: (_ for _ in ()).throw(RuntimeError("vector")))
    else: monkeypatch.setattr("app.services.chat_pdf_production_import_service._fts_status", lambda _r: {"status":"broken","ready":False})
    with pytest.raises(RuntimeError): import_document_to_production(import_job_id="x", document_type="paper", runtime=runtime)
    assert len(calls) == (0 if failure == "verify" else 1)

def test_body_write_then_raise_rolls_back(tmp_path, seams, monkeypatch):
    runtime, db = _runtime(tmp_path, _seed_body(tmp_path/"db.sqlite", fail=True)); calls=[]; monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", _rollback(db,calls))
    with pytest.raises(RuntimeError, match="body_failed_after_write"): import_document_to_production(import_job_id="x", document_type="paper", runtime=runtime)
    assert len(calls)==1

def test_body_before_write_has_no_rollback(tmp_path, seams, monkeypatch):
    runtime, db = _runtime(tmp_path, lambda *_: (_ for _ in ()).throw(RuntimeError("body"))); calls=[]; monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", _rollback(db,calls))
    with pytest.raises(RuntimeError): import_document_to_production(import_job_id="x", document_type="paper", runtime=runtime)
    assert calls == []

def test_ambiguous_delta_fails_closed(tmp_path, seams, monkeypatch):
    runtime, db = _runtime(tmp_path, _seed_body(tmp_path/"db.sqlite", count=2, fail=True)); calls=[]; monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", _rollback(db,calls))
    with pytest.raises(RuntimeError, match="ambiguous"): import_document_to_production(import_job_id="x", document_type="paper", runtime=runtime)
    assert calls == []

def test_rollback_failure_is_hard_error(tmp_path, seams, monkeypatch):
    runtime, _ = _runtime(tmp_path, _seed_body(tmp_path/"db.sqlite")); monkeypatch.setattr("app.services.chat_pdf_production_import_service._rollback_document", lambda *a: (_ for _ in ()).throw(RuntimeError("fail")))
    monkeypatch.setattr("app.services.chat_pdf_production_import_service.chat_local_note_import_service.import_local_notes", lambda **_: (_ for _ in ()).throw(RuntimeError("notes")))
    with pytest.raises(RuntimeError, match="chat_import_rollback_failed"): import_document_to_production(import_job_id="x", document_type="paper", runtime=runtime)

@pytest.mark.parametrize("document_type", ["paper", "book", "report"])
def test_production_orchestrator_requires_opt_in_for_all_document_types(document_type: str):
    with pytest.raises(RuntimeError, match="opt_in"):
        import_document_to_production(import_job_id="missing", document_type=document_type)
