"""Compatibility surface for the decomposed chapter review implementation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from app.core.paths import DEFAULT_DB_PATH
from app.services.chapter_note_correction_prompt_service import (
    FORBIDDEN_REVIEW_KEYS,
    ChapterNoteCorrectionPromptError,
    build_chapter_note_correction_prompt_package,
    build_chapter_note_correction_canary_subscope_package,
    build_chapter_note_correction_scoped_package,
    note_correction_dry_run_safety_flags,
    validate_chapter_note_correction_review,
)
from app.services.unit_note_object_processing_service import table_exists

from .contracts import (
    NOTE_CORRECTION_REVIEW_TABLE,
    NOTE_CORRECTION_REVIEW_ITEM_TABLE,
    NOTE_CLASSIFICATION_REVIEW_TABLE,
    NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE,
    NOTE_CORRECTION_SAVE_CONTEXT,
    NOTE_CLASSIFICATION_SAVE_CONTEXT,
    OBJECT_CANDIDATE_DRAFT_SAVE_CONTEXT,
    OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_CONTEXT,
    NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION,
    NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION,
    NOTE_CLASSIFICATION_REVIEW_SAVE_SCHEMA_VERSION,
    OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION,
    OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION,
    PRODUCTION_DB_WRITE_ENABLED,
    PRODUCTION_REVIEW_SAVE_CANARY_ENV,
    PRODUCTION_REVIEW_SAVE_SECTION_ENV,
    PRODUCTION_REVIEW_SAVE_SECTION84_PN68_ENV,
    PRODUCTION_REVIEW_SAVE_SECTION_TARGET_ENV,
    PRODUCTION_NOTE_CLASSIFICATION_SAVE_ENV,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_SAVE_ENV,
    PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_ENV,
    OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE,
    OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE,
    OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE,
    OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE,
    PRODUCTION_REVIEW_CANARY_WRITE_TABLES,
    PRODUCTION_NOTE_CLASSIFICATION_WRITE_TABLES,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_WRITE_TABLES,
    PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_WRITE_TABLES,
    PRODUCTION_REVIEW_SECTION_DOCUMENT_ID,
    PRODUCTION_REVIEW_SECTION_CHAPTER_ID,
    PRODUCTION_REVIEW_SECTION_ALLOWED_SCOPES,
    PRODUCTION_REVIEW_SECTION_DEFERRED_SCOPES,
    PRODUCTION_REVIEW_SECTION84_PN68_SCOPE_ID,
    PRODUCTION_REVIEW_SECTION84_PN68_EXPECTED_COUNT,
    PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY,
    PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID,
    PRODUCTION_REVIEW_SECTION84_PN68_ALLOWED_STATUSES,
    PRODUCTION_REVIEW_SECTION84_PN68_REQUIRED_WARNINGS,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_DOCUMENT_ID,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_CHAPTER_ID,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_QUARANTINED_COUNT,
    PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID,
    PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_ID,
    MERGED_NOTE_CORRECTION_SECTION_ORDER,
    NOTE_CLASSIFICATION_LABEL_ORDER,
    NOTE_CLASSIFICATION_LABELS,
    NOTE_CLASSIFICATION_MANUAL_CONFIDENCE_ORDER,
    NOTE_CLASSIFICATION_MANUAL_CONFIDENCES,
    OBJECT_CANDIDATE_DRY_RUN_TYPE_ORDER,
    OBJECT_CANDIDATE_DRY_RUN_TYPES,
    OBJECT_CANDIDATE_DRY_RUN_QUARANTINE_LABELS,
    RELATION_CANDIDATE_DRY_RUN_SCHEMA_VERSION,
    RELATION_CANDIDATE_VALIDATOR_CONTRACT_VERSION,
    RELATION_CANDIDATE_DRY_RUN_TYPE_ORDER,
    RELATION_CANDIDATE_DRY_RUN_TYPES,
    USER_TAG_AGREEMENTS,
)

from .classification import (
    _blocked_classification_package,
    _chapter_context_summary,
    _corrected_notes_for_classification,
    build_chapter_note_classification_dry_run_package,
    build_chapter_note_classification_package,
)

from .classification_contracts import (
    _classification_section_distribution,
    _excerpt,
    _manual_forbidden_field_names,
    _phase7a_note_summaries,
    _phase7a_pn68_status,
    build_note_classification_copy_ready_prompt,
    build_note_classification_copy_ready_prompt_legacy,
    build_phase7a_classification_prompt_preview,
    build_phase7a_classification_validator_contract,
    classification_taxonomy,
    classification_taxonomy_audit,
    note_classification_output_schema,
    phase7b_manual_classification_expected_schema,
)

from .classification_persistence import (
    _blocked_classification_save_response,
    _classification_payload_requests_forbidden_side_effects,
    build_note_classification_review_save_readiness,
    build_note_classification_review_save_request_gate,
    save_chapter_note_classification_manual_json,
)

from .classification_validation import (
    _classification_candidate_index,
    _classification_candidate_key,
    _classification_stats,
    _forbidden_keys,
    _manual_forbidden_keys,
    _manual_warning_list,
    _match_classification_candidate,
    _normalize_classification_item,
    _parse_manual_classification_payload,
    _pn68_warning_handled,
    _validate_classification_summary,
    validate_chapter_note_classification_manual_json,
    validate_chapter_note_classification_review,
)

from .gates import (
    _build_note_correction_review_save_section84_pn68_request_gate,
    _review_save_block_reason,
    build_note_correction_review_production_canary_preflight,
    build_note_correction_review_save_readiness,
    build_note_correction_review_save_request_gate,
)

from .loading import (
    _saved_classification_review_exists,
    _saved_review_payload,
    _saved_review_section_sort_key,
    _section_index,
    build_note_correction_production_db_snapshot,
    build_saved_note_correction_review_state,
    load_merged_saved_note_correction_review,
    load_saved_note_classification_review,
    load_saved_note_correction_review,
    load_saved_object_candidate_draft_review,
    load_saved_object_candidate_human_review,
)

from .normalization import (
    _canary_subscope_response_metadata,
    _candidate_order_for_item,
    _canonical_review_json,
    _hash_json_for_contract,
    _hash_review,
    _ignored_review_item_summary,
    _int_or_none,
    _is_confidence_score,
    _item_key,
    _loads,
    _merge_preview_summary,
    _merge_scope_complete,
    _normalized_item_from_saved_row,
    _parse_review_payload,
    _primary_note_identity_keys,
    _saved_correction_review_merge_complete,
    _str_or_none,
    _utc_now,
)

from .object_candidates import (
    _phase7d_candidate_from_term,
    _phase7d_candidates_for_classified_item,
    _phase7d_chunk_ids,
    _phase7d_confidence_for_label,
    _phase7d_excerpt,
    _phase7d_fallback_note_candidate,
    _phase7d_known_object_terms,
    _phase7d_known_object_terms_for_text,
    _phase7d_note_phrase,
    _phase7d_object_candidate_summary,
    _phase7d_page_labels,
    _phase7d_quarantined_item,
    _phase7d_slug,
    build_chapter_object_candidate_dry_run_package,
    build_phase7d_object_candidate_prompt_preview,
    build_phase7d_object_candidate_validator_contract,
    build_tri_source_object_package_preview,
    phase7d_object_candidate_extraction_policy,
    validate_phase7d_object_candidate_dry_run_candidates,
)

from .object_draft_review import (
    _blocked_object_candidate_draft_save_response,
    _coerce_object_candidate_draft_package,
    _object_candidate_draft_hash_payload,
    build_object_candidate_draft_save_readiness,
    build_object_candidate_draft_save_request_gate,
    save_chapter_object_candidate_dry_run_drafts,
    validate_object_candidate_draft_save_payload,
)

from .object_human_review import (
    _blocked_object_candidate_human_review_save_response,
    _coerce_object_candidate_human_review_payload,
    _phase7f_workbench_candidate,
    build_object_candidate_human_review_save_readiness,
    build_object_candidate_human_review_save_request_gate,
    build_object_candidate_human_review_workbench,
    build_phase7f_object_candidate_human_review_fixture,
    save_object_candidate_human_review,
    validate_object_candidate_human_review_payload,
)

from .object_review_common import (
    _object_candidate_payload_requests_forbidden_side_effects,
    _truthy_true,
)

from .persistence import (
    _active_correction_review_row,
    _active_correction_review_row_ro,
    _chapter_exists,
    _connect_ro_existing,
    _connect_rw_existing,
    _is_default_research_db_path,
    _latest_object_candidate_draft_review_row,
    _latest_object_candidate_human_review_row,
    _latest_saved_classification_review_row,
    _latest_saved_correction_review_row,
    _note_classification_review_schema_ready,
    _note_correction_review_schema_ready,
    _object_candidate_draft_review_schema_ready,
    _object_candidate_human_review_schema_ready,
    _saved_review_row,
    _table_column_names,
)

from .pipeline import (
    _blocked_canary_plan_response,
    _blocked_save_response,
    _build_note_correction_save_audit_trace,
    _expected_correction_package,
    build_note_correction_review_save_canary_plan,
    save_chapter_note_correction_review,
)

from .relations import (
    _clean_int_list,
    _clean_string_list,
    _phase7g_excluded_candidate_summary,
    _phase7g_page_sort_key,
    _phase7g_relation_candidate_for_pair,
    _phase7g_relation_candidates_for_approved,
    _phase7g_relation_confidence,
    _phase7g_relation_rationale,
    _phase7g_relation_source_candidate,
    _phase7g_relation_type_and_direction,
    _relation_dry_run_safety_flags,
    build_chapter_relation_candidate_dry_run_package,
    build_phase7g_relation_candidate_prompt_preview,
    build_phase7g_relation_candidate_validator_contract,
    phase7g_relation_candidate_extraction_policy,
    validate_relation_candidate_dry_run_package,
)

from .safety import (
    review_pipeline_safety_flags,
)

from .schema import (
    build_note_classification_review_schema_audit,
    build_object_candidate_draft_review_schema_audit,
    build_object_candidate_human_review_schema_audit,
    ensure_chapter_review_tables,
    ensure_note_classification_review_tables,
    ensure_object_candidate_draft_review_tables,
    ensure_object_candidate_human_review_tables,
    note_classification_review_schema_sql,
    note_correction_review_schema_sql,
    object_candidate_draft_review_schema_sql,
    object_candidate_human_review_schema_sql,
)

from .validation import (
    _candidate_original_index,
    _human_audit_index,
    _normalize_human_audit_items,
    _normalize_one_human_audit_item,
    _normalized_review_root,
)

from .write_policy import (
    is_production_note_classification_save_enabled,
    is_production_object_candidate_draft_save_enabled,
    is_production_object_candidate_human_review_save_enabled,
    is_production_review_save_canary_enabled,
    is_production_review_save_section84_pn68_enabled,
    is_production_review_save_section_enabled,
    production_review_save_section_target,
    production_review_save_section_target_expected_count,
)
