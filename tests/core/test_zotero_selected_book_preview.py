from __future__ import annotations

import hashlib
import gc
import sqlite3
from pathlib import Path

import pytest

from app.services import (
    import_duplicate_check_service,
    zotero_selected_book_preview_service as service,
)


def make_environment(
    tmp_path,
    *,
    pdf_count=1,
):
    zotero_root = tmp_path / "zotero"
    storage_root = zotero_root / "storage"
    storage_root.mkdir(parents=True)

    snapshot = tmp_path / "zotero.sqlite"
    research_db = tmp_path / "research.db"

    config = {
        "zotero_data_dir": str(zotero_root),
        "zotero_storage_root": str(storage_root),
        "zotero_db_snapshot": str(snapshot),
    }

    with sqlite3.connect(snapshot) as connection:
        connection.executescript(
            """
            CREATE TABLE fields (
                fieldID INTEGER PRIMARY KEY,
                fieldName TEXT NOT NULL
            );

            CREATE TABLE itemDataValues (
                valueID INTEGER PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE itemData (
                itemID INTEGER,
                fieldID INTEGER,
                valueID INTEGER
            );

            CREATE TABLE items (
                itemID INTEGER PRIMARY KEY,
                itemTypeID INTEGER,
                dateAdded TEXT,
                dateModified TEXT,
                clientDateModified TEXT,
                libraryID INTEGER,
                key TEXT,
                version INTEGER,
                synced INTEGER
            );

            CREATE TABLE itemTypes (
                itemTypeID INTEGER PRIMARY KEY,
                typeName TEXT NOT NULL
            );

            CREATE TABLE itemAttachments (
                itemID INTEGER PRIMARY KEY,
                parentItemID INTEGER,
                linkMode INTEGER,
                contentType TEXT,
                charsetID INTEGER,
                path TEXT,
                syncState INTEGER,
                storageModTime INTEGER,
                storageHash TEXT,
                lastProcessedModificationTime INTEGER,
                lastRead INTEGER
            );

            CREATE TABLE itemAnnotations (
                itemID INTEGER PRIMARY KEY,
                parentItemID INTEGER,
                type TEXT,
                authorName TEXT,
                text TEXT,
                comment TEXT,
                color TEXT,
                pageLabel TEXT,
                sortIndex TEXT,
                position TEXT,
                isExternal INTEGER
            );

            CREATE TABLE itemNotes (
                itemID INTEGER PRIMARY KEY,
                parentItemID INTEGER,
                note TEXT,
                title TEXT
            );

            CREATE TABLE deletedItems (
                itemID INTEGER PRIMARY KEY
            );
            """
        )

        connection.execute(
            """
            INSERT INTO fields(fieldID, fieldName)
            VALUES (1, 'title')
            """
        )
        connection.execute(
            "INSERT INTO itemTypes(itemTypeID, typeName) VALUES (1, 'book'), (2, 'attachment')"
        )

        connection.execute(
            """
            INSERT INTO itemDataValues(valueID, value)
            VALUES (1, 'Selected Test Book')
            """
        )

        connection.execute(
            """
            INSERT INTO items(
                itemID,
                itemTypeID,
                dateAdded,
                dateModified,
                libraryID,
                key,
                version,
                synced
            )
            VALUES (
                1,
                1,
                '2026-07-01 00:00:00',
                '2026-07-20 00:00:00',
                1,
                'BOOKKEY1',
                5,
                1
            )
            """
        )

        connection.execute(
            """
            INSERT INTO itemData(
                itemID,
                fieldID,
                valueID
            )
            VALUES (1, 1, 1)
            """
        )

        for index in range(pdf_count):
            item_id = 10 + index
            attachment_key = f"PDFKEY{index + 1}"
            file_name = f"book{index + 1}.pdf"

            connection.execute(
                """
                INSERT INTO items(
                    itemID,
                    itemTypeID,
                    dateAdded,
                    dateModified,
                    libraryID,
                    key,
                    version,
                    synced
                )
                VALUES (
                    ?,
                    2,
                    '2026-07-01 00:00:00',
                    '2026-07-21 00:00:00',
                    1,
                    ?,
                    3,
                    1
                )
                """,
                (item_id, attachment_key),
            )

            connection.execute(
                """
                INSERT INTO itemAttachments(
                    itemID,
                    parentItemID,
                    linkMode,
                    contentType,
                    path
                )
                VALUES (?, 1, 0, 'application/pdf', ?)
                """,
                (
                    item_id,
                    f"storage:{file_name}",
                ),
            )

            target = (
                storage_root
                / attachment_key
                / file_name
            )

            target.parent.mkdir(parents=True)
            target.write_bytes(
                (
                    "%PDF-1.4\n"
                    f"test-{index}\n"
                ).encode("ascii")
            )

        if pdf_count:
            connection.execute(
                """
                INSERT INTO items(
                    itemID,
                    itemTypeID,
                    dateAdded,
                    dateModified,
                    libraryID,
                    key,
                    version,
                    synced
                )
                VALUES (
                    20,
                    3,
                    '2026-07-02 00:00:00',
                    '2026-07-22 00:00:00',
                    1,
                    'ANNKEY1',
                    4,
                    1
                )
                """
            )

            connection.execute(
                """
                INSERT INTO itemAnnotations(
                    itemID,
                    parentItemID,
                    type,
                    text,
                    comment,
                    pageLabel,
                    position
                )
                VALUES (
                    20,
                    10,
                    'highlight',
                    'Original selected text',
                    'My annotation comment',
                    '12',
                    '{"pageIndex":11}'
                )
                """
            )

            connection.execute(
                """
                INSERT INTO items(
                    itemID,
                    itemTypeID,
                    dateAdded,
                    dateModified,
                    libraryID,
                    key,
                    version,
                    synced
                )
                VALUES (
                    30,
                    4,
                    '2026-07-03 00:00:00',
                    '2026-07-23 00:00:00',
                    1,
                    'NOTEKEY1',
                    2,
                    1
                )
                """
            )

            connection.execute(
                """
                INSERT INTO itemNotes(
                    itemID,
                    parentItemID,
                    note,
                    title
                )
                VALUES (
                    30,
                    1,
                    '<p>Parent child note</p>',
                    'Reading note'
                )
                """
            )

            connection.execute(
                """
                INSERT INTO items(
                    itemID,
                    itemTypeID,
                    dateAdded,
                    dateModified,
                    libraryID,
                    key,
                    version,
                    synced
                )
                VALUES (
                    31,
                    4,
                    '2026-07-04 00:00:00',
                    '2026-07-24 00:00:00',
                    1,
                    'NOTEKEY2',
                    2,
                    1
                )
                """
            )

            connection.execute(
                """
                INSERT INTO itemNotes(
                    itemID,
                    parentItemID,
                    note,
                    title
                )
                VALUES (
                    31,
                    10,
                    '<p>PDF child note</p>',
                    'PDF note'
                )
                """
            )

        connection.commit()

    with sqlite3.connect(research_db) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT,
                document_type TEXT,
                content_layer TEXT,
                source_path TEXT,
                pdf_path TEXT,
                zotero_key TEXT
            );

            CREATE TABLE knowledge_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER
            );
            """
        )

    return {
        "snapshot": snapshot,
        "research_db": research_db,
        "config": config,
        "storage_root": storage_root,
    }


@pytest.fixture(autouse=True)
def clear_preview_cache():
    service._clear_preview_cache_for_tests()
    yield
    service._clear_preview_cache_for_tests()


@pytest.fixture
def no_pdf_parser(monkeypatch):
    monkeypatch.setattr(
        import_duplicate_check_service,
        "first_pages_text_fingerprint",
        lambda *_args, **_kwargs: (
            None,
            {
                "status": "skipped",
                "warnings": [],
            },
        ),
    )

    monkeypatch.setattr(
        import_duplicate_check_service,
        "_file_page_size_meta",
        lambda path: {
            "file_size": Path(path).stat().st_size,
            "page_count": 321,
        },
    )
    monkeypatch.setattr(
        service.pdf_extraction_strategy_service,
        "build_pdf_extraction_plan",
        lambda *_args, **_kwargs: {
            "extractor_strategy": "native_text",
            "text_quality_score": 90.0,
            "quality_reasons": ["native_text_layer_quality_acceptable"],
            "text_quality_metrics": {},
            "converted_markdown_status": "not_required",
            "converted_markdown_path": None,
            "converted_markdown_pdf_sha256": None,
            "converted_markdown_sha256": None,
            "estimated_pages": 321,
            "estimated_chunks": 36,
            "extraction_ready": True,
            "blockers": [],
            "warnings": [],
        },
    )


def test_single_pdf_preview_is_ready_and_preserves_note_semantics(
    tmp_path,
    no_pdf_parser,
):
    env = make_environment(tmp_path)

    result = service.build_selected_book_preview(
        zotero_item_key="BOOKKEY1",
        snapshot_path=env["snapshot"],
        db_path=env["research_db"],
        config=env["config"],
        now_ts=1000,
        token_ttl_seconds=300,
    )

    assert result["status"] == "ready"
    assert result["zotero_item"]["title"] == "Selected Test Book"
    assert result["zotero_item"]["item_type"] == "book"

    assert (
        result["selected_attachment"][
            "zotero_attachment_key"
        ]
        == "PDFKEY1"
    )

    assert result[
        "selected_attachment"
    ]["page_count"] == 321

    assert result["annotation_count"] == 1
    assert result["annotation_comment_count"] == 1
    assert result["child_note_count"] == 2

    annotation = result["annotations"][0]

    assert (
        annotation["selected_text"]
        == "Original selected text"
    )
    assert (
        annotation["source_comment"]
        == "My annotation comment"
    )

    assert annotation["pdf_page"] == 12
    assert annotation["page_label"] == "12"
    assert (
        annotation["position_json"]
        == '{"pageIndex":11}'
    )

    assert annotation[
        "source_identity"
    ] == "zotero:1:annotation:ANNKEY1"

    child_identities = {
        note["source_identity"]
        for note in result["child_notes"]
    }

    assert child_identities == {
        "zotero:1:child_note:NOTEKEY1",
        "zotero:1:child_note:NOTEKEY2",
    }

    assert result["preview_token"]
    assert result["db_write_performed"] is False
    assert result["zotero_db_write_performed"] is False
    assert result["vector_store_write_performed"] is False
    assert result["fts_write_performed"] is False


def test_no_pdf_is_rejected(
    tmp_path,
):
    env = make_environment(
        tmp_path,
        pdf_count=0,
    )

    with pytest.raises(
        service.ZoteroSelectedBookPreviewError,
        match="no direct PDF",
    ) as exc_info:
        service.build_selected_book_preview(
            zotero_item_key="BOOKKEY1",
            snapshot_path=env["snapshot"],
            db_path=env["research_db"],
            config=env["config"],
        )

    assert exc_info.value.code == "no_pdf_attachment"


def test_multiple_pdfs_require_explicit_attachment_choice(
    tmp_path,
):
    env = make_environment(
        tmp_path,
        pdf_count=2,
    )

    result = service.build_selected_book_preview(
        zotero_item_key="BOOKKEY1",
        snapshot_path=env["snapshot"],
        db_path=env["research_db"],
        config=env["config"],
    )

    assert result[
        "status"
    ] == "attachment_choice_required"

    assert result["attachment_count"] == 2
    assert result["preview_token"] is None


def test_explicit_attachment_choice_is_honored(
    tmp_path,
    no_pdf_parser,
):
    env = make_environment(
        tmp_path,
        pdf_count=2,
    )

    result = service.build_selected_book_preview(
        zotero_item_key="BOOKKEY1",
        zotero_attachment_key="PDFKEY2",
        snapshot_path=env["snapshot"],
        db_path=env["research_db"],
        config=env["config"],
    )

    assert (
        result["selected_attachment"][
            "zotero_attachment_key"
        ]
        == "PDFKEY2"
    )

    # Annotation and PDF-child-note fixtures belong to PDFKEY1.
    assert result["annotation_count"] == 0
    assert result["child_note_count"] == 1


def test_snapshot_connection_is_read_only_query_only(
    tmp_path,
):
    env = make_environment(tmp_path)

    with service.open_snapshot_readonly(
        env["snapshot"]
    ) as connection:
        query_only = connection.execute(
            "PRAGMA query_only"
        ).fetchone()[0]

        assert int(query_only) == 1

        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "CREATE TABLE forbidden_write(id INTEGER)"
            )


def test_preview_token_expires(
    tmp_path,
    no_pdf_parser,
):
    env = make_environment(tmp_path)

    result = service.build_selected_book_preview(
        zotero_item_key="BOOKKEY1",
        snapshot_path=env["snapshot"],
        db_path=env["research_db"],
        config=env["config"],
        now_ts=1000,
        token_ttl_seconds=10,
    )

    with pytest.raises(
        service.ZoteroSelectedBookPreviewError
    ) as exc_info:
        service.validate_selected_book_preview_token(
            result["preview_token"],
            now_ts=1010,
        )

    assert (
        exc_info.value.code
        == "preview_token_expired"
    )


def test_preview_token_detects_source_drift(
    tmp_path,
    no_pdf_parser,
):
    env = make_environment(tmp_path)

    result = service.build_selected_book_preview(
        zotero_item_key="BOOKKEY1",
        snapshot_path=env["snapshot"],
        db_path=env["research_db"],
        config=env["config"],
        now_ts=1000,
        token_ttl_seconds=100,
    )

    with sqlite3.connect(
        env["snapshot"]
    ) as connection:
        connection.execute(
            """
            UPDATE itemAnnotations
            SET comment = 'Changed after preview'
            WHERE itemID = 20
            """
        )

        connection.execute(
            """
            UPDATE items
            SET
                dateModified =
                    '2026-07-25 00:00:00',
                version = 5
            WHERE itemID = 20
            """
        )

        connection.commit()

    with pytest.raises(
        service.ZoteroSelectedBookPreviewError
    ) as exc_info:
        service.validate_selected_book_preview_token(
            result["preview_token"],
            now_ts=1001,
        )

    assert (
        exc_info.value.code
        == "preview_source_drift"
    )


def test_content_addressed_preview_stays_bound_to_capture_a(
    tmp_path,
    no_pdf_parser,
):
    env = make_environment(tmp_path)
    capture_a = env["snapshot"]
    revision_a = hashlib.sha256(capture_a.read_bytes()).hexdigest()
    preview_a = service.build_selected_book_preview(
        zotero_item_key="BOOKKEY1",
        snapshot_path=capture_a,
        zotero_source_revision=revision_a,
        db_path=env["research_db"],
        config=env["config"],
        now_ts=1000,
        token_ttl_seconds=100,
    )

    capture_b = tmp_path / "capture-b.sqlite"
    source = sqlite3.connect(capture_a)
    destination = sqlite3.connect(capture_b)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    with sqlite3.connect(capture_b) as connection:
        connection.execute(
            "UPDATE itemAnnotations SET comment='Capture B comment' WHERE itemID=20"
        )
    revision_b = hashlib.sha256(capture_b.read_bytes()).hexdigest()
    preview_b = service.build_selected_book_preview(
        zotero_item_key="BOOKKEY1",
        snapshot_path=capture_b,
        zotero_source_revision=revision_b,
        db_path=env["research_db"],
        config=env["config"],
        now_ts=1001,
        issue_token=False,
    )
    resolved_a = service.resolve_selected_book_preview_token(
        preview_a["preview_token"],
        now_ts=1002,
    )

    assert preview_b["annotations"][0]["source_comment"] == "Capture B comment"
    assert resolved_a["annotations"][0]["source_comment"] == "My annotation comment"
    assert resolved_a["zotero_source_revision"] == revision_a
    assert resolved_a["zotero_source_revision"] != revision_b


@pytest.mark.parametrize("mutation", ["tamper", "delete"])
def test_content_addressed_preview_capture_loss_fails_closed(
    tmp_path,
    no_pdf_parser,
    mutation,
):
    env = make_environment(tmp_path)
    capture = env["snapshot"]
    revision = hashlib.sha256(capture.read_bytes()).hexdigest()
    preview = service.build_selected_book_preview(
        zotero_item_key="BOOKKEY1",
        snapshot_path=capture,
        zotero_source_revision=revision,
        db_path=env["research_db"],
        config=env["config"],
        now_ts=1000,
        token_ttl_seconds=100,
    )
    if mutation == "tamper":
        capture.write_bytes(capture.read_bytes() + b"tamper")
    else:
        gc.collect()
        capture.unlink()

    with pytest.raises(service.ZoteroSelectedBookPreviewError) as error:
        service.resolve_selected_book_preview_token(
            preview["preview_token"],
            now_ts=1001,
        )
    assert error.value.code == "preview_source_drift"
    assert error.value.details["cause_code"] == "zotero_source_revision_corrupt"


def test_duplicate_item_key_across_libraries_is_rejected(
    tmp_path,
    no_pdf_parser,
):
    env = make_environment(tmp_path)

    with sqlite3.connect(
        env["snapshot"]
    ) as connection:
        connection.execute(
            """
            INSERT INTO items(
                itemID,
                itemTypeID,
                dateAdded,
                dateModified,
                libraryID,
                key,
                version,
                synced
            )
            VALUES (
                90,
                1,
                '2026-07-01 00:00:00',
                '2026-07-20 00:00:00',
                2,
                'BOOKKEY1',
                1,
                1
            )
            """
        )

        connection.commit()

    with pytest.raises(
        service.ZoteroSelectedBookPreviewError
    ) as exc_info:
        service.build_selected_book_preview(
            zotero_item_key="BOOKKEY1",
            snapshot_path=env["snapshot"],
            db_path=env["research_db"],
            config=env["config"],
        )

    assert (
        exc_info.value.code
        == "zotero_item_key_ambiguous"
    )

    assert (
        exc_info.value.details[
            "matching_library_count"
        ]
        == 2
    )


def test_unknown_attachment_is_rejected(
    tmp_path,
    no_pdf_parser,
):
    env = make_environment(tmp_path)

    with pytest.raises(
        service.ZoteroSelectedBookPreviewError
    ) as exc_info:
        service.build_selected_book_preview(
            zotero_item_key="BOOKKEY1",
            zotero_attachment_key="NOTOWNED",
            snapshot_path=env["snapshot"],
            db_path=env["research_db"],
            config=env["config"],
        )

    assert (
        exc_info.value.code
        == "attachment_not_owned_by_item"
    )

def test_non_bibliographic_child_is_rejected_with_real_item_type(
    tmp_path,
):
    env = make_environment(tmp_path)

    with sqlite3.connect(
        env["snapshot"]
    ) as connection:
        connection.execute(
            (
                "INSERT INTO itemTypes("
                "itemTypeID, typeName"
                ") VALUES (99, 'annotation')"
            )
        )
        connection.execute(
            (
                "UPDATE items "
                "SET itemTypeID = 99 "
                "WHERE key = 'BOOKKEY1'"
            )
        )
        connection.commit()

    with pytest.raises(
        service.ZoteroSelectedBookPreviewError
    ) as exc_info:
        service.build_selected_book_preview(
            zotero_item_key="BOOKKEY1",
            snapshot_path=env["snapshot"],
            db_path=env["research_db"],
            config=env["config"],
        )

    assert exc_info.value.code == (
        "zotero_item_type_unsupported"
    )
    assert exc_info.value.details[
        "item_type"
    ] == "annotation"


def test_journal_article_parent_is_supported_for_preview(
    tmp_path,
    no_pdf_parser,
):
    env = make_environment(tmp_path)
    with sqlite3.connect(env["snapshot"]) as connection:
        connection.execute(
            "INSERT INTO itemTypes(itemTypeID,typeName) VALUES(99,'journalArticle')"
        )
        connection.execute(
            "UPDATE items SET itemTypeID=99 WHERE key='BOOKKEY1'"
        )
        connection.commit()

    result = service.build_selected_book_preview(
        zotero_item_key="BOOKKEY1",
        snapshot_path=env["snapshot"],
        db_path=env["research_db"],
        config=env["config"],
        now_ts=1000,
    )
    assert result["status"] == "ready"
    assert result["zotero_item"]["item_type"] == "journalArticle"
    assert result["preview_token"]


def test_extraction_blocker_prevents_preview_token(
    tmp_path,
    no_pdf_parser,
    monkeypatch,
):
    env = make_environment(tmp_path)
    monkeypatch.setattr(
        service.pdf_extraction_strategy_service,
        "build_pdf_extraction_plan",
        lambda *_args, **_kwargs: {
            "extractor_strategy": "high_quality_pdf_to_markdown",
            "text_quality_score": 10.0,
            "quality_reasons": ["high_empty_page_ratio:1.000"],
            "text_quality_metrics": {"empty_page_ratio": 1.0},
            "converted_markdown_status": "converter_unavailable",
            "converted_markdown_path": None,
            "converted_markdown_pdf_sha256": None,
            "converted_markdown_sha256": None,
            "estimated_pages": 0,
            "estimated_chunks": 0,
            "extraction_ready": False,
            "blockers": [{"code": "required_extraction_models_missing"}],
            "warnings": ["high_quality_pdf_to_markdown_unavailable"],
        },
    )
    result = service.build_selected_book_preview(
        zotero_item_key="BOOKKEY1",
        snapshot_path=env["snapshot"],
        db_path=env["research_db"],
        config=env["config"],
        now_ts=1000,
    )
    assert result["extraction_ready"] is False
    assert result["estimated_chunks"] == 0
    assert result["blockers"] == [
        {"code": "required_extraction_models_missing"}
    ]
    assert result["preview_token"] is None

def test_reused_markdown_preview_reports_prepared_structure(
    tmp_path,
    no_pdf_parser,
    monkeypatch,
):
    env = make_environment(tmp_path)
    converted = tmp_path / "verified.md"
    converted.write_text(
        "<!-- SOURCE_PDF_SHA256: "
        + ("a" * 64)
        + " -->\n\n<!-- PDF_PAGE: 1 -->\n\n# Chapter 1\n\nBody.",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        service.pdf_extraction_strategy_service,
        "build_pdf_extraction_plan",
        lambda *_args, **_kwargs: {
            "extractor_strategy": "high_quality_pdf_to_markdown",
            "text_quality_score": 99.0,
            "quality_reasons": ["converted_markdown_quality_acceptable"],
            "text_quality_metrics": {},
            "converted_markdown_status": "reused_sha_verified",
            "converted_markdown_path": str(converted),
            "converted_markdown_pdf_sha256": "a" * 64,
            "converted_markdown_sha256": "b" * 64,
            "converted_markdown_page_markers": 798,
            "converted_markdown_characters": 1_447_692,
            "estimated_pages": 798,
            "estimated_chunks": 805,
            "extraction_ready": True,
            "blockers": [],
            "warnings": [],
        },
    )

    class Prepared:
        estimated_chunk_count = 3594
        chapters = [object()] * 35
        page_marker_count = 798
        detection_method = "pdf_outline"
        binding_rate = 1.0

    monkeypatch.setattr(
        service.book_import_service,
        "prepare_book_import_from_markdown",
        lambda *_args, **_kwargs: Prepared(),
    )

    result = service.build_selected_book_preview(
        zotero_item_key="BOOKKEY1",
        snapshot_path=env["snapshot"],
        db_path=env["research_db"],
        config=env["config"],
        now_ts=1000,
        token_ttl_seconds=300,
    )

    assert result["estimated_chunks"] == 3594
    assert result["chapter_count"] == 35
    assert result["page_marker_count"] == 798
    assert result["detection_method"] == "pdf_outline"
    assert result["binding_rate"] == 1.0
    assert result["preview_token"]
