from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArchiveDocumentsRequest(StrictRequest):
    document_ids: list[int] = Field(..., min_length=1, max_length=5)

    @field_validator("document_ids")
    @classmethod
    def unique_positive_ids(cls, values: list[int]) -> list[int]:
        normalized = [int(value) for value in values]
        if any(value < 1 for value in normalized):
            raise ValueError("document_ids must be positive")
        if len(set(normalized)) != len(normalized):
            raise ValueError("document_ids must be unique")
        return normalized


class DeletionOptions(StrictRequest):
    preserve_external_pdf: bool = True
    delete_managed_pdf: bool = False
    preserve_personal_notes: bool = True
    preserve_zotero_notes: bool = True
    preserve_shared_objects: bool = True
    delete_exclusive_derived_objects: bool = True
    delete_generated_markdown: bool = True
    delete_generated_cache: bool = True


class ManualPreservationAcknowledgment(StrictRequest):
    blocker_type: str = Field(..., min_length=1, max_length=128)
    record_ids: list[int] = Field(..., min_length=1, max_length=100)
    document_id: int = Field(..., ge=1)
    preservation_artifact_directory: str = Field(..., min_length=1, max_length=2048)
    preservation_manifest_sha256: str = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9A-Fa-f]{64}$",
    )
    acknowledged_by: str = Field(..., min_length=1, max_length=256)
    acknowledgment_text: str = Field(..., min_length=1, max_length=4096)

    @field_validator("record_ids")
    @classmethod
    def unique_positive_record_ids(cls, values: list[int]) -> list[int]:
        normalized = [int(value) for value in values]
        if any(value < 1 for value in normalized):
            raise ValueError("record_ids must be positive")
        if len(set(normalized)) != len(normalized):
            raise ValueError("record_ids must be unique")
        return normalized


class DeletionPreviewRequest(StrictRequest):
    deletion_options: DeletionOptions = Field(default_factory=DeletionOptions)
    manual_preservation_acknowledgment: ManualPreservationAcknowledgment | None = None


class DeleteDocumentRequest(StrictRequest):
    document_id: int = Field(..., ge=1)
    preview_token: str = Field(..., min_length=32, max_length=256)
    expected_document_revision: str = Field(..., min_length=64, max_length=64)
    confirmation_text: str = Field(..., min_length=1, max_length=512)
    deletion_options: DeletionOptions = Field(default_factory=DeletionOptions)
    manual_preservation_acknowledgment: ManualPreservationAcknowledgment | None = None


class DeleteDocumentsBatchRequest(StrictRequest):
    document_ids: list[int] = Field(..., min_length=1, max_length=5)
    requests: list[DeleteDocumentRequest] = Field(..., min_length=1, max_length=5)
    confirmation_text: str = Field(..., min_length=1, max_length=64)

    @field_validator("document_ids")
    @classmethod
    def unique_batch_ids(cls, values: list[int]) -> list[int]:
        normalized = [int(value) for value in values]
        if any(value < 1 for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("document_ids must contain unique positive ids")
        return normalized
