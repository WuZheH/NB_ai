from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.services.retrieval.fts_status_service import DEFAULT_QUERY_ALIASES_PATH
from app.services.retrieval.query_normalizer import normalize_for_search


@dataclass(frozen=True)
class CuratedAliasMatch:
    concept: str
    matched_term: str
    expanded_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "concept": self.concept,
            "matched_term": self.matched_term,
            "expanded_terms": list(self.expanded_terms),
            "reason": "curated_alias_match",
        }


def expand_curated_aliases(
    normalized_query: str,
    *,
    aliases_path: str | Path = DEFAULT_QUERY_ALIASES_PATH,
) -> list[CuratedAliasMatch]:
    config = load_alias_config(Path(aliases_path))
    normalized_input = normalize_for_search(normalized_query)
    matches: list[CuratedAliasMatch] = []
    for entry in config["aliases"]:
        normalized_terms = _normalized_terms(entry["terms"])
        if normalized_input not in normalized_terms:
            continue
        matches.append(
            CuratedAliasMatch(
                concept=str(entry["concept"]),
                matched_term=normalized_input,
                expanded_terms=tuple(
                    term for term in normalized_terms
                    if term != normalized_input
                ),
            )
        )
    return matches


def load_alias_config(path: Path = DEFAULT_QUERY_ALIASES_PATH) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("aliases"), list):
        raise ValueError("retrieval alias config must contain an aliases list")
    owners: dict[str, str] = {}
    for entry in payload["aliases"]:
        if not isinstance(entry, dict) or not entry.get("concept"):
            raise ValueError("each alias entry requires a concept")
        terms = entry.get("terms")
        if not isinstance(terms, list) or len(terms) < 2:
            raise ValueError(f"alias concept requires at least two terms: {entry.get('concept')}")
        for term in _normalized_terms(terms):
            owner = owners.setdefault(term, str(entry["concept"]))
            if owner != str(entry["concept"]):
                raise ValueError(f"alias term belongs to multiple concepts: {term}")
    return payload


def _normalized_terms(terms: list[object]) -> list[str]:
    result: list[str] = []
    for value in terms:
        term = normalize_for_search(str(value))
        if term and term not in result:
            result.append(term)
    return result
