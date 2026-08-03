from __future__ import annotations

from typing import Any


class EvidenceWorkflowError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_detail(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            **self.details,
            "db_write_performed": False,
            "production_db_write_performed": False,
            "zotero_db_write_performed": False,
            "vector_write_performed": False,
            "llm_called": False,
        }
