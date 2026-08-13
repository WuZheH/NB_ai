from __future__ import annotations

import re
from dataclasses import dataclass


MOCK_OR_TEST_MARKERS = (
    "mock",
    "acceptance",
    "synthetic",
    "placeholder",
    "dummy",
    "fixture",
    "phase7",
    "phase8a",
    "phase9a",
    "phase9b",
    "phase9c",
    "phase10a",
)
WEAK_MARKERS = (
    "synthetic",
    "test",
    "acceptance",
    "dummy",
    "placeholder",
    "fixture",
)
FALSE_POSITIVE_PHRASES = (
    "synthetic data generation",
    "synthetic aperture radar",
    "technology acceptance model",
    "test-time adaptation",
    "statistical test",
    "a/b test",
    "test set",
    "dummy variable",
    "dummy coding",
    "pytest fixture",
)
STRONG_LEVEL = "strong_mock_marker"
WEAK_LEVEL = "weak_possible_marker"
FALSE_POSITIVE_LEVEL = "likely_false_positive"


@dataclass(frozen=True)
class EvidenceHygieneIssue:
    source: str
    title: str
    reason: str
    matched_markers: list[str]
    document_id: int | None = None
    chunk_id: int | None = None
    note_id: int | None = None
    tag_id: int | None = None
    relation_id: int | None = None


@dataclass(frozen=True)
class MarkerClassification:
    level: str | None
    matched_markers: list[str]


def is_mock_or_test_text(text: str | None) -> bool:
    return bool(match_mock_or_test_markers(text))


def match_mock_or_test_markers(text: str | None) -> list[str]:
    classification = classify_mock_or_test_markers(text)
    if classification.level != STRONG_LEVEL:
        return []
    return classification.matched_markers


def classify_mock_or_test_markers(text: str | None) -> MarkerClassification:
    if not text:
        return MarkerClassification(level=None, matched_markers=[])
    lowered = text.lower()
    tokens = _tokens(lowered)
    strong_markers = _strong_markers(lowered, tokens)
    if strong_markers:
        return MarkerClassification(level=STRONG_LEVEL, matched_markers=strong_markers)

    weak_markers = _weak_markers(lowered, tokens)
    if not weak_markers:
        return MarkerClassification(level=None, matched_markers=[])
    if _contains_false_positive_phrase(lowered):
        return MarkerClassification(level=FALSE_POSITIVE_LEVEL, matched_markers=weak_markers)
    return MarkerClassification(level=WEAK_LEVEL, matched_markers=weak_markers)


def build_hygiene_issue(
    source: str,
    title: str,
    text: str,
    *,
    document_id: int | None = None,
    chunk_id: int | None = None,
    note_id: int | None = None,
    tag_id: int | None = None,
    relation_id: int | None = None,
) -> EvidenceHygieneIssue | None:
    matched_markers = match_mock_or_test_markers(text)
    if not matched_markers:
        return None
    return EvidenceHygieneIssue(
        source=source,
        title=title,
        reason="mock/test/acceptance evidence is excluded from real Research Session evidence.",
        matched_markers=matched_markers,
        document_id=document_id,
        chunk_id=chunk_id,
        note_id=note_id,
        tag_id=tag_id,
        relation_id=relation_id,
    )


def serialize_hygiene_issue(issue: EvidenceHygieneIssue) -> dict[str, object]:
    return {
        "source": issue.source,
        "title": issue.title,
        "reason": issue.reason,
        "matched_markers": list(issue.matched_markers),
        "document_id": issue.document_id,
        "chunk_id": issue.chunk_id,
        "note_id": issue.note_id,
        "tag_id": issue.tag_id,
        "relation_id": issue.relation_id,
    }


def _has_token(text: str, token: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text))


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text))


def _strong_markers(text: str, tokens: set[str]) -> list[str]:
    matched: list[str] = []
    matched.extend(_known_acceptance_script_markers(text))

    if re.search(r"(?<![a-z0-9])mock[\s_\-]+paper(?![a-z0-9])", text):
        matched.append("mock_paper")
    if re.search(r"(?<![a-z0-9])mock[\s_\-]+edsr(?![a-z0-9])", text):
        matched.append("mock_edsr")

    phase_markers = _phase_markers(text)
    has_phase = bool(phase_markers)
    if has_phase:
        matched.extend(phase_markers)
        if "acceptance" in tokens:
            matched.append("phase+acceptance")
        if "mock" in tokens:
            matched.append("phase+mock")
        if "test" in tokens:
            matched.append("phase+test")

    if "acceptance" in tokens and "mock" in tokens:
        matched.append("acceptance+mock")
    if "acceptance" in tokens and ("placeholder" in tokens or "fixture" in tokens):
        matched.append("acceptance+fixture_or_placeholder")

    return _dedupe(matched)


def _known_acceptance_script_markers(text: str) -> list[str]:
    markers: list[str] = []
    for match in re.findall(r"(?<![a-z0-9])phase[0-9]+[a-z]?_(?:acceptance|retrieval_eval)(?![a-z0-9])", text):
        markers.append(match)
    return markers


def _phase_markers(text: str) -> list[str]:
    markers = re.findall(r"(?<![a-z0-9])phase[\s_\-]*[0-9]+(?:\.[0-9]+)?[a-z]?(?![a-z0-9])", text)
    return [" ".join(marker.replace("_", " ").replace("-", " ").split()) for marker in markers]


def _weak_markers(text: str, tokens: set[str]) -> list[str]:
    weak: list[str] = []
    for marker in WEAK_MARKERS:
        if marker in tokens:
            weak.append(marker)
    if "mock" in tokens:
        weak.append("mock")
    if _phase_markers(text):
        weak.append("phase")
    return _dedupe(weak)


def _contains_false_positive_phrase(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.replace("_", " ").strip())
    return any(phrase in normalized for phrase in FALSE_POSITIVE_PHRASES)


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped
