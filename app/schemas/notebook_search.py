from __future__ import annotations

from typing import Any

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

    @field_validator("document_ids", mode="before")
    @classmethod
    def strict_document_ids(cls, values: Any) -> list[int]:
        """Validate raw document_ids elements BEFORE Pydantic type coercion.

        Public contract:
          - ``[]`` → no document restriction (search all documents).
          - Only non-empty positive integers are accepted.
          - ``bool``, ``str``, ``float``, ``0`` and negative values are rejected
            (422 via FastAPI validation).
          - Duplicates are removed, preserving order.
        """
        if values is None:
            return []
        if not isinstance(values, list):
            raise ValueError("document_ids must be a list of positive integers")
        result: list[int] = []
        seen: set[int] = set()
        for value in values:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("document_ids must contain positive integers")
            if value < 1:
                raise ValueError("document_ids must contain positive integers")
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result


__all__ = ["NotebookSearchRequest", "NotebookSearchResponse"]
