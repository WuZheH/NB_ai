import sqlite3
from pathlib import Path

from app.services import chat_tool_service, zotero_library_service


def _fixture(tmp_path, monkeypatch):
    snapshot = tmp_path / "zotero.sqlite"
    data = tmp_path / "data"
    storage = tmp_path / "storage"
    data.mkdir()
    storage.mkdir()
    (storage / "ATT1").mkdir()
    (storage / "ATT1" / "paper.pdf").write_bytes(b"%PDF fixture")

    with sqlite3.connect(snapshot) as connection:
        connection.executescript(
            """
            CREATE TABLE items(
                itemID INTEGER PRIMARY KEY,
                key TEXT,
                itemTypeID INTEGER,
                dateModified TEXT
            );
            CREATE TABLE itemTypes(itemTypeID INTEGER PRIMARY KEY,typeName TEXT);
            CREATE TABLE fields(fieldID INTEGER PRIMARY KEY,fieldName TEXT);
            CREATE TABLE itemData(itemID INTEGER,fieldID INTEGER,valueID INTEGER);
            CREATE TABLE itemDataValues(valueID INTEGER PRIMARY KEY,value TEXT);
            CREATE TABLE itemAttachments(
                itemID INTEGER PRIMARY KEY,parentItemID INTEGER,path TEXT,contentType TEXT
            );
            CREATE TABLE itemAnnotations(itemID INTEGER PRIMARY KEY,parentItemID INTEGER);
            CREATE TABLE itemNotes(itemID INTEGER PRIMARY KEY,parentItemID INTEGER);
            CREATE TABLE creators(
                creatorID INTEGER PRIMARY KEY,firstName TEXT,lastName TEXT,fieldMode INTEGER
            );
            CREATE TABLE itemCreators(itemID INTEGER,creatorID INTEGER,orderIndex INTEGER);
            CREATE TABLE tags(tagID INTEGER PRIMARY KEY,name TEXT);
            CREATE TABLE itemTags(itemID INTEGER,tagID INTEGER);
            """
        )
        connection.executemany(
            "INSERT INTO itemTypes VALUES(?,?)",
            [
                (1, "book"),
                (2, "journalArticle"),
                (3, "attachment"),
                (4, "annotation"),
                (5, "note"),
            ],
        )
        connection.executemany(
            "INSERT INTO fields VALUES(?,?)",
            [
                (1, "title"),
                (2, "date"),
                (3, "abstractNote"),
                (4, "DOI"),
                (5, "shortTitle"),
                (6, "publicationTitle"),
                (7, "extra"),
            ],
        )
        connection.executemany(
            "INSERT INTO items VALUES(?,?,?,?)",
            [
                (1, "BOOK1", 1, "2026-01-01"),
                (2, "FMF4LBDE", 2, "2026-01-02"),
                (3, "EMPTY", 2, "2026-01-03"),
                (10, "ATT1", 3, "2026-02-01"),
                (11, "ATT2", 3, "2026-02-02"),
                (20, "ANN1", 4, "2026-03-01"),
                (21, "ANN2", 4, "2026-03-02"),
                (30, "NOTE1", 5, "2026-04-01"),
                (40, "ORPHANANN", 4, "2026-05-01"),
                (41, "ORPHANATT", 3, "2026-05-02"),
            ],
        )
        connection.executemany(
            "INSERT INTO itemDataValues VALUES(?,?)",
            [
                (1, "Deep Learning"),
                (2, "Generating Diverse and Natural 3D Human Motions From Text"),
                (3, "2022"),
                (4, "The HumanML3D benchmark and dataset"),
                (5, ""),
                (6, "10.1109/CVPR52688.2022.00511"),
                (7, "Human motion generation"),
                (8, "CVPR"),
                (9, "Citation Key: Guo2022HumanML3D"),
            ],
        )
        connection.executemany(
            "INSERT INTO itemData VALUES(?,?,?)",
            [
                (1, 1, 1),
                (2, 1, 2),
                (2, 2, 3),
                (2, 3, 4),
                (3, 1, 5),
                (2, 4, 6),
                (2, 5, 7),
                (2, 6, 8),
                (2, 7, 9),
            ],
        )
        connection.executemany(
            "INSERT INTO itemAttachments VALUES(?,?,?,?)",
            [
                (10, 2, "storage:paper.pdf", "application/pdf"),
                (11, 2, "storage:supplement.pdf", "application/pdf"),
                (41, None, "storage:orphan.pdf", "application/pdf"),
            ],
        )
        connection.executemany(
            "INSERT INTO itemAnnotations VALUES(?,?)",
            [(20, 10), (21, 11), (40, None)],
        )
        connection.execute("INSERT INTO itemNotes VALUES(30,2)")
        connection.execute("INSERT INTO creators VALUES(1,'Chuan','Guo',0)")
        connection.execute("INSERT INTO itemCreators VALUES(2,1,0)")
        connection.execute("INSERT INTO tags VALUES(1,'motion synthesis')")
        connection.execute("INSERT INTO itemTags VALUES(2,1)")
        connection.commit()

    monkeypatch.setattr(
        zotero_library_service.zotero_source_cache_service,
        "_load_config",
        lambda: {
            "zotero_db_snapshot": str(snapshot),
            "zotero_data_dir": str(data),
            "zotero_storage_root": str(storage),
        },
    )
    monkeypatch.setattr(
        zotero_library_service.zotero_source_cache_service,
        "_project_path",
        lambda path: Path(path),
    )
    return snapshot


def test_journal_filter_returns_only_top_level_parent(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    result = zotero_library_service.list_parent_items(
        document_type="journalArticle", db_path=None
    )
    assert [item["parent_key"] for item in result["items"]] == ["FMF4LBDE"]
    assert all(item["item_type"] == "journalArticle" for item in result["items"])
    assert all(item["kind"] == "zotero" for item in result["items"])


def test_annotation_attachment_and_orphans_never_become_candidates(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    result = zotero_library_service.list_parent_items(db_path=None)
    keys = {item["parent_key"] for item in result["items"]}
    assert keys == {"BOOK1", "FMF4LBDE"}
    assert not keys.intersection({"ATT1", "ATT2", "ANN1", "ANN2", "ORPHANANN", "ORPHANATT"})
    assert result["warnings"] == [
        {"code": "zotero_parent_without_title_hidden", "count": 1},
        {"code": "zotero_orphan_child_items_hidden", "count": 2},
    ]


def test_two_level_annotation_and_direct_child_note_aggregation(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    item = zotero_library_service.list_parent_items(
        query="HumanML3D", db_path=None
    )["items"][0]
    assert item["parent_key"] == "FMF4LBDE"
    assert item["attachment_keys"] == ["ATT1", "ATT2"]
    assert item["primary_pdf_attachment_key"] == "ATT1"
    assert item["annotation_count"] == 2
    assert item["child_note_count"] == 1
    assert item["recent_activity_at"] == "2026-04-01"


def test_empty_annotation_titles_do_not_pollute_search(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    assert zotero_library_service.list_parent_items(
        query="ANN1", db_path=None
    )["count"] == 0


def test_title_author_and_normalized_abbreviation_search(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    for query in (
        "Generating Diverse",
        "Chuan Guo",
        "Guo",
        "HumanML3D",
        "human ml3d",
        "FMF4LBDE",
        "ATT1",
        "10.1109/CVPR52688.2022.00511",
        "Human motion generation",
        "CVPR",
        "Guo2022HumanML3D",
        "motion synthesis",
    ):
        result = zotero_library_service.list_parent_items(query=query, db_path=None)
        assert [item["parent_key"] for item in result["items"]] == ["FMF4LBDE"]


def test_no_annotations_is_explicit_zero(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    item = zotero_library_service.list_parent_items(
        query="Deep Learning", db_path=None
    )["items"][0]
    assert item["annotation_count"] == 0
    assert item["child_note_count"] == 0


def test_chat_tool_passes_document_type_to_parent_service(monkeypatch):
    captured = {}

    def fake_list_parent_items(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "scope": "zotero", "count": 0, "items": [], "truncated": False}

    monkeypatch.setattr(
        chat_tool_service.zotero_library_service,
        "list_parent_items",
        fake_list_parent_items,
    )
    chat_tool_service.list_library(
        scope="zotero", document_type="journalArticle", limit=7
    )
    assert captured == {
        "query": None,
        "document_type": "journalArticle",
        "status": "active",
        "limit": 7,
        "db_path": chat_tool_service.DEFAULT_DB_PATH,
    }


def test_limit_and_truncated_are_computed_after_parent_filtering(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    result = zotero_library_service.list_parent_items(
        status="all",
        limit=1,
        db_path=None,
    )
    assert result["count"] == 1
    assert result["total_matches"] == 2
    assert result["truncated"] is True
    assert result["items"][0]["item_type"] in {"book", "journalArticle"}


def test_status_filters_available_and_imported_without_silent_ignore(
    tmp_path,
    monkeypatch,
):
    _fixture(tmp_path, monkeypatch)
    research_db = tmp_path / "research.db"
    with sqlite3.connect(research_db) as connection:
        connection.executescript(
            """
            CREATE TABLE documents(id INTEGER PRIMARY KEY,zotero_key TEXT);
            CREATE TABLE document_sources(
                document_id INTEGER,
                zotero_item_key TEXT
            );
            INSERT INTO documents(id,zotero_key) VALUES(7,'FMF4LBDE');
            INSERT INTO document_sources(document_id,zotero_item_key)
            VALUES(7,'FMF4LBDE');
            """
        )
    imported = zotero_library_service.list_parent_items(
        status="imported",
        db_path=research_db,
    )
    available = zotero_library_service.list_parent_items(
        status="available",
        db_path=research_db,
    )
    assert [item["parent_key"] for item in imported["items"]] == ["FMF4LBDE"]
    assert imported["items"][0]["status"] == "imported"
    assert imported["items"][0]["imported_document_id"] == 7
    assert [item["parent_key"] for item in available["items"]] == ["BOOK1"]


def test_parent_key_and_attachment_key_have_deterministic_exact_priority(
    tmp_path,
    monkeypatch,
):
    _fixture(tmp_path, monkeypatch)
    for query in ("FMF4LBDE", "ATT2"):
        result = zotero_library_service.list_parent_items(
            query=query,
            db_path=None,
        )
        assert [item["parent_key"] for item in result["items"]] == ["FMF4LBDE"]
