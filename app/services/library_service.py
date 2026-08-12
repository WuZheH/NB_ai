from __future__ import annotations

from app.services import library_core_service as _core
from app.services.library_core_service import (
    DocumentPdfSource,
    LibraryChunkPreview,
    LibraryDocumentDetail,
    LibraryEvidenceItem,
    LibraryGroupedSearchChunk,
    LibraryGroupedSearchDocument,
    LibraryHomeItem,
    LibraryLinkedChunkItem,
    LibraryNoteItem,
    LibraryNotePreview,
    LibraryRelatedNoteItem,
    LibraryRelationItem,
    LibrarySearchResult,
    ReadLibraryDocumentSummary,
    EVIDENCE_SNIPPET_CHARS,
    GROUPED_SEARCH_MODE,
    GROUPED_SEARCH_SNIPPET_CHARS,
    HOME_DOCUMENT_TYPES,
    HOME_NOTE_TYPES,
    LINKED_CHUNKS_LIMIT,
    METADATA_CHUNK_MARKERS,
    QUERY_EXPANSIONS,
    READ_LIBRARY_DOCUMENT_TYPES,
    READ_LIBRARY_STATUSES,
    RELATED_NOTES_LIMIT,
    RELATED_RELATIONS_LIMIT,
    SAFE_PDF_ROOTS,
    TEST_DATA_METADATA_MARKERS,
    TEST_DATA_PATH_MARKERS,
    TEST_DATA_PREFIXES,
    TEST_DATA_TITLE_MARKERS,
    TOP_HEADINGS_LIMIT,
)
from app.services.library_document_service import (
    get_document_pdf_source,
    is_safe_pdf_path,
    is_test_library_record,
    resolve_document_pdf_path,
    resolve_safe_pdf_path,
    show_library_document,
)
from app.services.library_evidence_service import (
    evidence_locator_contract,
    is_metadata_chunk,
    is_metadata_chunk_text,
    normalize_evidence_text,
    show_library_chunk,
    show_library_evidence,
)
from app.services.library_home_service import get_library_home, list_read_books
from app.services.library_relation_service import show_library_note, show_library_notes
from app.services.library_search_service import (
    normalize_grouped_search_query,
    search_library,
    search_library_grouped,
)


def __getattr__(name: str):
    return getattr(_core, name)
