from __future__ import annotations

from typing import Any


FOUR_LAYER_BUCKETS = {"topic_tags", "problem_tags", "mechanism_tags", "inspiration_tags"}
CLEAR_MECHANISM_TERMS = (
    "contact constraint",
    "semantic alignment",
    "feature reuse",
    "multi-scale aggregation",
    "long-range dependency",
    "physical constraint",
    "physics-guided",
    "physics based projection",
    "physics-based projection",
    "self-similarity aggregation",
)
GENERIC_METHOD_FAMILIES = (
    "motion diffusion",
    "diffusion model",
    "transformer",
    "mdm",
)
INSPIRATION_TERMS = (
    "failure-driven",
    "metric-driven",
    "prior-driven",
    "analogy-driven",
    "architecture-driven",
    "data-driven",
    "constraint-driven",
    "efficiency-driven",
)


def map_legacy_tag_to_four_layer(raw_tag: Any) -> dict[str, Any]:
    source_tag = parse_legacy_tag(raw_tag)
    tag_type = source_tag["tag_type"]
    name = source_tag["name"]
    lowered = name.lower()

    if tag_type in {"task", "topic", "topic_tag"}:
        return _mapped(source_tag, "topic_tags", name, "medium", "task tags generally describe the research target")
    if tag_type in {"problem", "problem_tag", "limitation", "failure_case"}:
        return _mapped(source_tag, "problem_tags", name, "high", f"{tag_type} tags describe research pain points")
    if tag_type in {"mechanism", "mechanism_tag"}:
        return _mapped(source_tag, "mechanism_tags", name, "high", "mechanism tags already describe operating mechanisms")
    if tag_type in {"inspiration", "inspiration_tag"}:
        if any(term in lowered for term in INSPIRATION_TERMS):
            return _mapped(source_tag, "inspiration_tags", name, "high", "inspiration tag encodes a known inspiration path")
        return _unmapped(source_tag, "inspiration tag does not encode a known inspiration path")

    if tag_type == "method":
        if any(term in lowered for term in CLEAR_MECHANISM_TERMS) and not any(
            family == lowered for family in GENERIC_METHOD_FAMILIES
        ):
            return _mapped(
                source_tag,
                "mechanism_tags",
                name,
                "medium",
                "method name expresses a clear operating mechanism",
            )
        return _unmapped(
            source_tag,
            "method tags are ambiguous and generic method families must not blindly become mechanisms",
        )

    if tag_type == "concept":
        return _unmapped(
            source_tag,
            "concept tags are context-dependent and may indicate topic, problem, mechanism, or evaluation concept",
        )
    if tag_type == "metric":
        return _evaluation_context(
            source_tag,
            "metric tags describe evaluation context and must not become mechanisms automatically",
        )
    if tag_type == "dataset":
        return _evaluation_context(source_tag, "dataset tags are evaluation context, not four-layer tags by default")
    if tag_type == "model":
        return _unmapped(source_tag, "model tags may indicate topic or mechanism and require human review")
    if tag_type == "":
        return _unmapped(source_tag, "tag type is missing")
    return _unmapped(source_tag, f"unsupported legacy tag type: {tag_type}")


def map_legacy_tags_to_four_layer(raw_tags: list[Any]) -> list[dict[str, Any]]:
    return [map_legacy_tag_to_four_layer(raw_tag) for raw_tag in raw_tags]


def parse_legacy_tag(raw_tag: Any) -> dict[str, str]:
    if isinstance(raw_tag, dict):
        tag_type = str(raw_tag.get("tag_type") or raw_tag.get("type") or "").strip().lower()
        name = str(raw_tag.get("name") or raw_tag.get("tag") or "").strip()
        fallback_raw = f"{tag_type}:{name}" if tag_type else name
        raw = str(raw_tag.get("raw") or fallback_raw)
    else:
        raw = str(raw_tag).strip()
        if ":" in raw:
            tag_type, name = raw.split(":", 1)
            tag_type = tag_type.strip().lower()
            name = name.strip()
        else:
            tag_type = ""
            name = raw
    return {"tag_type": tag_type, "name": name, "raw": raw}


def _mapped(
    source_tag: dict[str, str],
    target_bucket: str,
    name: str,
    confidence: str,
    mapping_reason: str,
) -> dict[str, Any]:
    return {
        "source_tag": dict(source_tag),
        "target_bucket": target_bucket,
        "name": name,
        "status": "suggested",
        "confidence": confidence,
        "mapping_reason": mapping_reason,
        "needs_human_review": False,
    }


def _unmapped(source_tag: dict[str, str], mapping_reason: str) -> dict[str, Any]:
    return {
        "source_tag": dict(source_tag),
        "target_bucket": None,
        "name": source_tag["name"],
        "status": "unmapped",
        "confidence": "low",
        "mapping_reason": mapping_reason,
        "needs_human_review": True,
    }


def _evaluation_context(source_tag: dict[str, str], mapping_reason: str) -> dict[str, Any]:
    return {
        "source_tag": dict(source_tag),
        "target_bucket": "evaluation_context",
        "name": source_tag["name"],
        "status": "unmapped",
        "confidence": "low",
        "mapping_reason": mapping_reason,
        "needs_human_review": True,
    }
