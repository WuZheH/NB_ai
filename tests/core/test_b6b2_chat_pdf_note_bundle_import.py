from pathlib import Path

from app.services.chat_import_catalog_service import note_sources
import pytest
from app.services import chat_tool_service
from app.services import chat_local_note_import_service
import sqlite3, json
from datetime import datetime


def test_note_bundle_metadata_is_relative_and_hashed(tmp_path: Path):
    root = tmp_path / "inbox"
    root.mkdir()
    pdf = root / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    note = root / "paper.notes.md"
    note.write_text("note", encoding="utf-8")
    sources = note_sources(pdf=pdf, inbox_root=root)
    assert sources[0]["relative_path"] == "paper.notes.md"
    assert len(sources[0]["sha256"]) == 64
    assert "path" not in sources[0]

@pytest.mark.parametrize("mutation", ["add", "delete", "modify"])
def test_note_bundle_set_changes_are_detectable(tmp_path: Path, mutation: str):
    root = tmp_path / "inbox"; root.mkdir(); pdf = root / "paper.pdf"; pdf.write_bytes(b"%PDF")
    note = root / "paper.notes.md"; note.write_text("one", encoding="utf-8")
    before = tuple(note_sources(pdf=pdf, inbox_root=root))
    if mutation == "add": (root / "paper.notes" ).mkdir(); (root / "paper.notes" / "two.md").write_text("two", encoding="utf-8")
    elif mutation == "delete": note.unlink()
    else: note.write_text("changed", encoding="utf-8")
    assert tuple(note_sources(pdf=pdf, inbox_root=root)) != before

@pytest.mark.parametrize("mutation", ["add", "delete", "modify"])
def test_preview_confirmation_rejects_bundle_mutation(tmp_path: Path, mutation: str):
    inbox = tmp_path / "inbox"; inbox.mkdir(); pdf = inbox / "paper.pdf"; pdf.write_bytes(b"%PDF-1.4 fixture")
    note = inbox / "paper.notes.md"; note.write_text("original", encoding="utf-8"); calls=[]
    runtime = chat_tool_service.ChatToolRuntime(db_path=tmp_path/"db.sqlite", data_dir=tmp_path/"data", inbox_root=inbox,
        classify_pdf=lambda *_a, **_k: {"duplicate":False,"document_type":"paper","title":"Paper","signals":{"page_count":1}},
        commit_import=lambda **_: calls.append(1))
    preview = chat_tool_service.import_preview("paper.pdf", runtime=runtime); token = preview["confirmation_token"]
    if mutation == "add": (inbox / "paper.notes").mkdir(); (inbox / "paper.notes" / "new.md").write_text("new", encoding="utf-8")
    elif mutation == "delete": note.unlink()
    else: note.write_text("changed", encoding="utf-8")
    with pytest.raises(chat_tool_service.ChatToolError) as caught:
        chat_tool_service.import_document(confirmation_token=token, confirmed=True, runtime=runtime)
    assert caught.value.error_code == "chat_import_bundle_changed"; assert calls == []

def test_local_note_and_evidence_sql_contract(tmp_path: Path):
    db=tmp_path/"db.sqlite"; root=tmp_path/"root"; root.mkdir(); note=root/"paper.notes.md"; note.write_text("# Heading\nContent", encoding="utf-8")
    with sqlite3.connect(db) as c:
        c.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY); INSERT INTO documents VALUES(7); CREATE TABLE personal_notes(id INTEGER PRIMARY KEY,document_id INTEGER,note_type TEXT,scope_type TEXT,title TEXT,content TEXT,summary TEXT,source_path TEXT,content_hash TEXT,source_system TEXT,source_record_kind TEXT,source_identity TEXT,selected_text TEXT,source_comment TEXT,source_missing INTEGER,created_at TEXT,updated_at TEXT); CREATE TABLE note_evidence_links(id INTEGER PRIMARY KEY,note_id INTEGER,document_id INTEGER,chunk_id INTEGER,link_type TEXT,evidence_role TEXT,quote_text TEXT,confidence REAL,created_by TEXT,created_at TEXT,pdf_page INTEGER,page_label TEXT,source_locator_json TEXT,alignment_status TEXT,alignment_method TEXT,alignment_warnings_json TEXT,source_quote_hash TEXT);")
    result=chat_local_note_import_service.import_local_notes(db_path=db, document_id=7, note_files=[note], inbox_root=root)
    assert result["note_count"] == result["evidence_link_count"] == 1
    with sqlite3.connect(db) as c:
        row=c.execute("SELECT document_id,selected_text,source_comment,created_at,updated_at,source_identity FROM personal_notes").fetchone(); ev=c.execute("SELECT document_id,chunk_id,alignment_status,alignment_method,source_locator_json FROM note_evidence_links").fetchone()
    assert row[0]==7 and row[1]=="" and row[2]=="Content" and row[3]!="CURRENT_TIMESTAMP" and row[4]!="CURRENT_TIMESTAMP" and row[5]
    datetime.fromisoformat(row[3]); datetime.fromisoformat(row[4]); locator=json.loads(ev[4]); assert ev[:4]==(7,None,"document_only","chat_catalog_bundle") and {"relative_path","block_ordinal"} <= locator.keys()

def test_note_source_identity_is_root_independent(tmp_path: Path):
    a=tmp_path/"a"; b=tmp_path/"b"; a.mkdir(); b.mkdir(); (a/"paper.notes.md").write_text("same",encoding="utf-8"); (b/"paper.notes.md").write_text("same",encoding="utf-8")
    ra=chat_local_note_import_service._source_rows([a/"paper.notes.md"],a); rb=chat_local_note_import_service._source_rows([b/"paper.notes.md"],b)
    assert ra[0]["identity"] == rb[0]["identity"]
