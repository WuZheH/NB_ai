from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.external_api_config import ExternalApiConfig


ALLOWED_EXTERNAL_PAYLOAD_FIELDS = {
    "query",
    "snippet",
    "document_title",
    "heading_path",
    "chunk_id",
}
SENSITIVE_EXTERNAL_PAYLOAD_FIELDS = {
    "api_key",
    "authorization",
    "token",
    "pdf_path",
    "source_path",
    "zotero_key",
    "zotero_open_url",
    "pdf_open_url",
    "full_note_content",
    "note_content",
    "full_chunk_text",
    "chunk_text",
}


class ExternalApiDisabledError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalApiGuard:
    config: ExternalApiConfig

    def validate_external_enabled(self, feature: str) -> None:
        reason = self.explain_disabled_reason(feature)
        if reason is not None:
            raise ExternalApiDisabledError(reason)

    def sanitize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.reject_sensitive_fields(payload)
        sanitized: dict[str, Any] = {}
        for key in ALLOWED_EXTERNAL_PAYLOAD_FIELDS:
            if key not in payload:
                continue
            value = payload[key]
            if key == "snippet" and isinstance(value, str):
                sanitized[key] = value[: max(1, self.config.max_snippet_chars)]
            else:
                sanitized[key] = value
        return sanitized

    def reject_sensitive_fields(self, payload: dict[str, Any]) -> None:
        present = sorted(_collect_sensitive_field_names(payload))
        if present:
            raise ValueError(f"external payload contains sensitive fields: {', '.join(present)}")

    def build_audit_record(
        self,
        *,
        feature: str,
        action: str,
        provider: str | None = None,
        payload: dict[str, Any] | None = None,
        allowed: bool = False,
        called: bool = False,
        degraded_reason: str | None = None,
    ) -> dict[str, Any]:
        sanitized_payload = self.sanitize_payload(payload or {})
        return {
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
            "feature": feature,
            "action": action,
            "provider": provider,
            "allowed": allowed,
            "called": called,
            "payload_fields": sorted(sanitized_payload),
            "payload_preview": sanitized_payload,
            "degraded_reason": degraded_reason,
        }

    def explain_disabled_reason(self, feature: str) -> str | None:
        if not self.config.external_api_enabled:
            return "external_api_enabled=False，外部 API 默认关闭。"
        if feature == "rerank" and not self.config.allow_external_rerank:
            return "allow_external_rerank=False，外部 rerank 未启用。"
        if feature == "llm" and not self.config.allow_external_llm:
            return "allow_external_llm=False，外部 LLM 未启用。"
        if feature == "paper_search" and not self.config.allow_external_paper_search:
            return "allow_external_paper_search=False，外部论文搜索未启用。"
        if not self.config.provider:
            return "未配置外部 API provider。"
        return None


def _collect_sensitive_field_names(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in SENSITIVE_EXTERNAL_PAYLOAD_FIELDS:
                found.add(str(key))
            found.update(_collect_sensitive_field_names(nested))
    elif isinstance(value, list):
        for item in value:
            found.update(_collect_sensitive_field_names(item))
    return found
