"""Stable constants and compatibility contracts for chapter review."""

from __future__ import annotations

from typing import Any

from app.services.chapter_note_correction_prompt_service import (
    ChapterNoteCorrectionPromptError,
    note_correction_dry_run_safety_flags,
    note_correction_output_schema,
    note_correction_review_return_example,
    note_correction_review_return_schema,
)


NOTE_CORRECTION_REVIEW_TABLE = "note_correction_reviews"

NOTE_CORRECTION_REVIEW_ITEM_TABLE = "note_correction_review_items"

NOTE_CLASSIFICATION_REVIEW_TABLE = "note_classification_reviews"

NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE = "note_classification_review_items"

NOTE_CORRECTION_SAVE_CONTEXT = "save_note_correction_review_after_user_audit"

NOTE_CLASSIFICATION_SAVE_CONTEXT = "save_note_classification_review_after_manual_json_validation"

OBJECT_CANDIDATE_DRAFT_SAVE_CONTEXT = "save_object_candidate_drafts_after_dry_run_review"

OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_CONTEXT = "save_object_candidate_human_review_after_user_audit"

NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION = "r3_note_correction_review_save_v1"

NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION = "r3_note_correction_human_audit_v1"

NOTE_CLASSIFICATION_REVIEW_SAVE_SCHEMA_VERSION = "r3_note_classification_review_save_v1"

OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION = "r3_object_candidate_draft_save_v1"

OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION = "r3_object_candidate_human_review_v1"

PRODUCTION_DB_WRITE_ENABLED = False

PRODUCTION_REVIEW_SAVE_CANARY_ENV = "NOTEBOOK_AI_ENABLE_PRODUCTION_REVIEW_SAVE_CANARY"

PRODUCTION_REVIEW_SAVE_SECTION_ENV = "NOTEBOOK_AI_ENABLE_PRODUCTION_REVIEW_SECTION_SAVE"

PRODUCTION_REVIEW_SAVE_SECTION84_PN68_ENV = "NOTEBOOK_AI_ENABLE_PRODUCTION_REVIEW_SECTION84_PN68_SAVE"

PRODUCTION_REVIEW_SAVE_SECTION_TARGET_ENV = "NOTEBOOK_AI_PRODUCTION_REVIEW_SECTION_SAVE_TARGET"

PRODUCTION_NOTE_CLASSIFICATION_SAVE_ENV = "NOTEBOOK_AI_ENABLE_PRODUCTION_NOTE_CLASSIFICATION_SAVE"

PRODUCTION_OBJECT_CANDIDATE_DRAFT_SAVE_ENV = "NOTEBOOK_AI_ENABLE_PRODUCTION_OBJECT_CANDIDATE_DRAFT_SAVE"

PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_ENV = "NOTEBOOK_AI_ENABLE_PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE"

OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE = "object_candidate_draft_reviews"

OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE = "object_candidate_draft_review_items"

OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE = "object_candidate_human_reviews"

OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE = "object_candidate_human_review_items"

PRODUCTION_REVIEW_CANARY_WRITE_TABLES = (
    NOTE_CORRECTION_REVIEW_TABLE,
    NOTE_CORRECTION_REVIEW_ITEM_TABLE,
)

PRODUCTION_NOTE_CLASSIFICATION_WRITE_TABLES = (
    NOTE_CLASSIFICATION_REVIEW_TABLE,
    NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE,
)

PRODUCTION_OBJECT_CANDIDATE_DRAFT_WRITE_TABLES = (
    OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE,
    OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE,
)

PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_WRITE_TABLES = (
    OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE,
    OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE,
)

PRODUCTION_REVIEW_SECTION_DOCUMENT_ID = 10

PRODUCTION_REVIEW_SECTION_CHAPTER_ID = 69

PRODUCTION_REVIEW_SECTION_ALLOWED_SCOPES = {
    "section_8_2": 10,
    "section_8_5": 5,
    "section_8_6": 8,
    "section_8_7": 12,
}

PRODUCTION_REVIEW_SECTION_DEFERRED_SCOPES = {
    "section_8_3": "already_saved",
    "section_8_4": "pn68_deferred",
}

PRODUCTION_REVIEW_SECTION84_PN68_SCOPE_ID = "section_8_4"

PRODUCTION_REVIEW_SECTION84_PN68_EXPECTED_COUNT = 24

PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY = "SYNPN068"

PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID = "zinsp_zotero_annotation_00000000000000000000000000000000"

PRODUCTION_REVIEW_SECTION84_PN68_ALLOWED_STATUSES = {"unclear", "needs_revision"}

PRODUCTION_REVIEW_SECTION84_PN68_REQUIRED_WARNINGS = {
    "bbox_present_no_readable_layout_anchor",
    "document_resolved_but_no_page_text_match",
    "alignment_uncertain",
}

PRODUCTION_OBJECT_CANDIDATE_DRAFT_DOCUMENT_ID = 10

PRODUCTION_OBJECT_CANDIDATE_DRAFT_CHAPTER_ID = 69

PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID = "nclr_1595f273202e46069d8ba946778eb885"

PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT = 37

PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_QUARANTINED_COUNT = 1

PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID = "ocdr_2f8908674f7b4e85931bda71f473006e"

PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_ID = "ochr_35325ffb80714a4bae96b6411e29ae08"

MERGED_NOTE_CORRECTION_SECTION_ORDER = (
    "section_8_2",
    "section_8_3",
    "section_8_4",
    "section_8_5",
    "section_8_6",
    "section_8_7",
)

NOTE_CLASSIFICATION_LABEL_ORDER = (
    "memory_note",
    "connection_note",
    "mechanism_note",
    "research_idea_note",
    "unclear",
    "needs_manual_review",
)

NOTE_CLASSIFICATION_LABELS = set(NOTE_CLASSIFICATION_LABEL_ORDER)

NOTE_CLASSIFICATION_MANUAL_CONFIDENCE_ORDER = ("low", "medium", "high")

NOTE_CLASSIFICATION_MANUAL_CONFIDENCES = set(NOTE_CLASSIFICATION_MANUAL_CONFIDENCE_ORDER)

OBJECT_CANDIDATE_DRY_RUN_TYPE_ORDER = (
    "concept",
    "method",
    "algorithm",
    "model",
    "metric",
    "dataset",
    "theorem_or_principle",
    "mechanism_candidate",
    "research_problem",
    "experiment_candidate",
)

OBJECT_CANDIDATE_DRY_RUN_TYPES = set(OBJECT_CANDIDATE_DRY_RUN_TYPE_ORDER)

OBJECT_CANDIDATE_DRY_RUN_QUARANTINE_LABELS = {"unclear", "needs_manual_review"}

RELATION_CANDIDATE_DRY_RUN_SCHEMA_VERSION = "r3_relation_candidate_dry_run_v1"

RELATION_CANDIDATE_VALIDATOR_CONTRACT_VERSION = "r3_relation_candidate_validator_contract_v1"

RELATION_CANDIDATE_DRY_RUN_TYPE_ORDER = (
    "related_to",
    "contrasts_with",
    "supports",
    "refines",
    "uses_method",
    "has_component",
    "part_of",
    "evaluates_with_metric",
    "evaluated_on_dataset",
    "addresses_problem",
    "suggests_mechanism",
    "inspires_research_idea",
)

RELATION_CANDIDATE_DRY_RUN_TYPES = set(RELATION_CANDIDATE_DRY_RUN_TYPE_ORDER)

USER_TAG_AGREEMENTS = {
    "agrees",
    "disagrees",
    "partially_agrees",
    "no_user_type_tag",
}


_PIPELINE_EXPORTS = {
    "classification_taxonomy",
    "classification_taxonomy_audit",
    "note_classification_output_schema",
    "note_classification_review_schema_sql",
    "note_correction_review_schema_sql",
    "object_candidate_draft_review_schema_sql",
    "object_candidate_human_review_schema_sql",
    "phase7b_manual_classification_expected_schema",
    "review_pipeline_safety_flags",
}


def __getattr__(name: str) -> Any:
    if name == "review_pipeline_safety_flags":
        from .safety import review_pipeline_safety_flags

        return review_pipeline_safety_flags
    if name in {
        "classification_taxonomy",
        "classification_taxonomy_audit",
        "note_classification_output_schema",
        "phase7b_manual_classification_expected_schema",
    }:
        from . import classification_contracts

        return getattr(classification_contracts, name)
    if name in {
        "note_classification_review_schema_sql",
        "note_correction_review_schema_sql",
        "object_candidate_draft_review_schema_sql",
        "object_candidate_human_review_schema_sql",
    }:
        from . import schema

        return getattr(schema, name)
    raise AttributeError(name)


__all__ = [
    "NOTE_CORRECTION_REVIEW_TABLE",
    "NOTE_CORRECTION_REVIEW_ITEM_TABLE",
    "NOTE_CLASSIFICATION_REVIEW_TABLE",
    "NOTE_CLASSIFICATION_REVIEW_ITEM_TABLE",
    "NOTE_CORRECTION_SAVE_CONTEXT",
    "NOTE_CLASSIFICATION_SAVE_CONTEXT",
    "OBJECT_CANDIDATE_DRAFT_SAVE_CONTEXT",
    "OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_CONTEXT",
    "NOTE_CORRECTION_REVIEW_SAVE_SCHEMA_VERSION",
    "NOTE_CORRECTION_HUMAN_AUDIT_SCHEMA_VERSION",
    "NOTE_CLASSIFICATION_REVIEW_SAVE_SCHEMA_VERSION",
    "OBJECT_CANDIDATE_DRAFT_SAVE_SCHEMA_VERSION",
    "OBJECT_CANDIDATE_HUMAN_REVIEW_SCHEMA_VERSION",
    "PRODUCTION_DB_WRITE_ENABLED",
    "PRODUCTION_REVIEW_SAVE_CANARY_ENV",
    "PRODUCTION_REVIEW_SAVE_SECTION_ENV",
    "PRODUCTION_REVIEW_SAVE_SECTION84_PN68_ENV",
    "PRODUCTION_REVIEW_SAVE_SECTION_TARGET_ENV",
    "PRODUCTION_NOTE_CLASSIFICATION_SAVE_ENV",
    "PRODUCTION_OBJECT_CANDIDATE_DRAFT_SAVE_ENV",
    "PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_SAVE_ENV",
    "OBJECT_CANDIDATE_DRAFT_REVIEW_TABLE",
    "OBJECT_CANDIDATE_DRAFT_REVIEW_ITEM_TABLE",
    "OBJECT_CANDIDATE_HUMAN_REVIEW_TABLE",
    "OBJECT_CANDIDATE_HUMAN_REVIEW_ITEM_TABLE",
    "PRODUCTION_REVIEW_CANARY_WRITE_TABLES",
    "PRODUCTION_NOTE_CLASSIFICATION_WRITE_TABLES",
    "PRODUCTION_OBJECT_CANDIDATE_DRAFT_WRITE_TABLES",
    "PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_WRITE_TABLES",
    "PRODUCTION_REVIEW_SECTION_DOCUMENT_ID",
    "PRODUCTION_REVIEW_SECTION_CHAPTER_ID",
    "PRODUCTION_REVIEW_SECTION_ALLOWED_SCOPES",
    "PRODUCTION_REVIEW_SECTION_DEFERRED_SCOPES",
    "PRODUCTION_REVIEW_SECTION84_PN68_SCOPE_ID",
    "PRODUCTION_REVIEW_SECTION84_PN68_EXPECTED_COUNT",
    "PRODUCTION_REVIEW_SECTION84_PN68_ZOTERO_KEY",
    "PRODUCTION_REVIEW_SECTION84_PN68_SERVER_NOTE_ID",
    "PRODUCTION_REVIEW_SECTION84_PN68_ALLOWED_STATUSES",
    "PRODUCTION_REVIEW_SECTION84_PN68_REQUIRED_WARNINGS",
    "PRODUCTION_OBJECT_CANDIDATE_DRAFT_DOCUMENT_ID",
    "PRODUCTION_OBJECT_CANDIDATE_DRAFT_CHAPTER_ID",
    "PRODUCTION_OBJECT_CANDIDATE_DRAFT_SOURCE_REVIEW_ID",
    "PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_COUNT",
    "PRODUCTION_OBJECT_CANDIDATE_DRAFT_EXPECTED_QUARANTINED_COUNT",
    "PRODUCTION_OBJECT_CANDIDATE_DRAFT_REVIEW_ID",
    "PRODUCTION_OBJECT_CANDIDATE_HUMAN_REVIEW_ID",
    "MERGED_NOTE_CORRECTION_SECTION_ORDER",
    "NOTE_CLASSIFICATION_LABEL_ORDER",
    "NOTE_CLASSIFICATION_LABELS",
    "NOTE_CLASSIFICATION_MANUAL_CONFIDENCE_ORDER",
    "NOTE_CLASSIFICATION_MANUAL_CONFIDENCES",
    "OBJECT_CANDIDATE_DRY_RUN_TYPE_ORDER",
    "OBJECT_CANDIDATE_DRY_RUN_TYPES",
    "OBJECT_CANDIDATE_DRY_RUN_QUARANTINE_LABELS",
    "RELATION_CANDIDATE_DRY_RUN_SCHEMA_VERSION",
    "RELATION_CANDIDATE_VALIDATOR_CONTRACT_VERSION",
    "RELATION_CANDIDATE_DRY_RUN_TYPE_ORDER",
    "RELATION_CANDIDATE_DRY_RUN_TYPES",
    "USER_TAG_AGREEMENTS",
    "ChapterNoteCorrectionPromptError",
    "note_correction_dry_run_safety_flags",
    "note_correction_output_schema",
    "note_correction_review_return_example",
    "note_correction_review_return_schema",
    *_PIPELINE_EXPORTS,
]
