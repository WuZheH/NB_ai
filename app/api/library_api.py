from __future__ import annotations

from app.api.library.importing import (
    _pdf_backend_unavailable_response,
    classify_pdf_import,
    commit_pdf_import,
    preview_chaptered_import,
    preview_pdf_import_gate,
    start_pdf_repair_preview,
    draft_pdf_repair_plan,
    preview_pdf_import_gate_file,
    create_chaptered_import_job,
    get_import_job,
    cancel_import_job,
)
from app.api.library.books import (
    get_book_detail,
    get_book_chapter_workspace_state,
)
from app.api.library.management import (
    archive_documents,
    create_mutation_session,
    restore_documents,
)
from app.api.library.search import (
    read_shelf,
    search_library,
    search_embedding_sidecar,
    search_high_quality,
    search_reranker_sidecar,
    search_semantic_objects,
    vector_store_status,
    _annotate_read_shelf_duplicates,
    _duplicate_key,
    vector_store_search_passages,
    vector_store_search_objects,
)
from app.api.library.pdf import (
    document_pdf,
    _document_pdf_not_found_response,
)
from app.api.library.documents import (
    delete_document,
    delete_documents_batch,
    deletion_preview,
    deletion_preview_with_acknowledgment,
    zotero_link_candidates,
    document_detail,
)
from app.api.library.evidence import (
    evidence_detail,
    search_object_candidates,
    object_candidate_detail,
    evidence_object_candidates,
    zotero_annotation_candidates,
    evidence_pdf_location,
)
from app.api.library.router import router


__all__ = [name for name in globals() if not name.startswith("_")]
