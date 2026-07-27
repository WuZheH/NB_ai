import sqlite3
from pathlib import Path

from app.services import zotero_library_service

def _fixture(tmp_path, monkeypatch, titles=("EDSR",)):
    snap=tmp_path/"zotero.sqlite"; data=tmp_path/"data"; storage=tmp_path/"storage"; data.mkdir(); storage.mkdir()
    with sqlite3.connect(snap) as c:
        c.executescript("CREATE TABLE items(itemID INTEGER PRIMARY KEY,key TEXT,itemTypeID INTEGER); CREATE TABLE itemTypes(itemTypeID INTEGER PRIMARY KEY,typeName TEXT); CREATE TABLE fields(fieldID INTEGER PRIMARY KEY,fieldName TEXT); CREATE TABLE itemData(itemID INTEGER,fieldID INTEGER,valueID INTEGER); CREATE TABLE itemDataValues(valueID INTEGER PRIMARY KEY,value TEXT); CREATE TABLE itemAttachments(itemID INTEGER PRIMARY KEY,parentItemID INTEGER,path TEXT,contentType TEXT); CREATE TABLE itemAnnotations(itemID INTEGER,parentItemID INTEGER); CREATE TABLE itemNotes(itemID INTEGER,parentItemID INTEGER);")
        c.execute("INSERT INTO itemTypes VALUES(1,'book'),(2,'journalArticle')"); c.execute("INSERT INTO fields VALUES(1,'title')")
        for i,title in enumerate(titles,1):
            c.execute("INSERT INTO items VALUES(?,?,?)",(i,f"KEY{i}",1 if i==1 else 2)); c.execute("INSERT INTO itemDataValues VALUES(?,?)",(i,title)); c.execute("INSERT INTO itemData VALUES(?,?,?)",(i,1,i))
        c.execute("INSERT INTO items VALUES(10,'ATT1',3)")
        c.execute("INSERT INTO itemTypes VALUES(3,'attachment')")
        c.execute("INSERT INTO itemAttachments VALUES(10,1,'storage:paper.pdf','application/pdf')"); c.execute("INSERT INTO itemAnnotations VALUES(20,10)"); c.execute("INSERT INTO itemNotes VALUES(30,10)"); c.commit()
    monkeypatch.setattr(zotero_library_service.zotero_source_cache_service,"_load_config",lambda:{"zotero_db_snapshot":str(snap),"zotero_data_dir":str(data),"zotero_storage_root":str(storage)})
    monkeypatch.setattr(zotero_library_service.zotero_source_cache_service,"_project_path",lambda p:Path(p)); return snap

def test_zotero_scope_zero_match(tmp_path, monkeypatch):
    _fixture(tmp_path,monkeypatch); assert zotero_library_service.list_parent_items(query="PML")["count"]==0

def test_zotero_scope_single_and_real_item_type(tmp_path, monkeypatch):
    _fixture(tmp_path,monkeypatch); result=zotero_library_service.list_parent_items(query="EDSR"); assert result["items"][0]["item_type"]=="book"

def test_duplicate_title_remains_ambiguous(tmp_path, monkeypatch):
    _fixture(tmp_path,monkeypatch,titles=("EDSR","EDSR")); result=zotero_library_service.list_parent_items(query="EDSR"); assert result["count"]==2

def test_attachment_choices_are_safe(tmp_path, monkeypatch):
    _fixture(tmp_path,monkeypatch); item=zotero_library_service.list_parent_items(query="EDSR")["items"][0]; choice=item["attachment_choices"][0]; assert set(choice)=={"zotero_attachment_key","file_name","path_exists","content_type"}; assert "path" not in choice

def test_real_zotero_schema_attachment_key_comes_from_items(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    item = zotero_library_service.list_parent_items(query="EDSR")["items"][0]
    assert item["attachment_choices"][0]["zotero_attachment_key"] == "ATT1"

def test_annotation_and_child_note_counts(tmp_path, monkeypatch):
    _fixture(tmp_path,monkeypatch); item=zotero_library_service.list_parent_items(query="EDSR")["items"][0]; assert item["annotation_count"]==1 and item["child_note_count"]==1
