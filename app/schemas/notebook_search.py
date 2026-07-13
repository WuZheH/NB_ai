from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.retrieval.result_contracts import (
    NOTEBOOK_SOURCE_TYPES,
    NotebookSearchResponse,
    NotebookSourceType,
)


class NotebookSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(default=12, ge=1, le=50)
    source_types: list[NotebookSourceType] = Field(
        default_factory=lambda: list(NOTEBOOK_SOURCE_TYPES)
    )
    document_ids: list[int] = Field(default_factory=list)
    include_context: bool = True

    @field_validator("query")
    @classmethod
    def compact_query(cls, value: str) -> str:
        compact = " ".join(value.split())
        if not compact:
            raise ValueError("query must not be empty")
        return compact

    @field_validator("source_types")
    @classmethod
    def unique_source_types(
        cls, values: list[NotebookSourceType]
    ) -> list[NotebookSourceType]:
        if not values:
            raise ValueError("source_types must contain at least one supported source")
        return list(dict.fromkeys(values))

    @field_validator("document_ids")
    @classmethod
    def unique_document_ids(cls, values: list[int]) -> list[int]:
        invalid = [value for value in values if value < 1]
        if invalid:
            raise ValueError("document_ids must contain positive integers")
        return list(dict.fromkeys(values))


__all__ = ["NotebookSearchRequest", "NotebookSearchResponse"]
