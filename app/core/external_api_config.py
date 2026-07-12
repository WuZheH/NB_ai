from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExternalApiConfig:
    external_api_enabled: bool = False
    allow_external_rerank: bool = False
    allow_external_llm: bool = False
    allow_external_paper_search: bool = False
    provider: str | None = None
    max_snippet_chars: int = 500
    redact_paths: bool = True
    audit_external_calls: bool = True


DEFAULT_EXTERNAL_API_CONFIG = ExternalApiConfig()
