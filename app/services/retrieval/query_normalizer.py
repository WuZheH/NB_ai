from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass


_DASHES = str.maketrans({
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
})
_CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*|[\u3400-\u9fff]+")


@dataclass(frozen=True)
class NormalizedQuery:
    original_query: str
    normalized_query: str
    phrases: tuple[str, ...]
    terms: tuple[str, ...]
    identifier_variants: tuple[str, ...]
    contains_cjk: bool
    short_query: bool

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["phrases"] = list(self.phrases)
        payload["terms"] = list(self.terms)
        payload["identifier_variants"] = list(self.identifier_variants)
        return payload


def normalize_query(query: str) -> NormalizedQuery:
    original = str(query or "").strip()
    if not original:
        raise ValueError("query must not be empty")
    canonical = _canonical_unicode(original)
    phrases = tuple(
        value
        for value in (
            normalize_for_search(match)
            for match in re.findall(r'"([^"]+)"', canonical)
        )
        if value
    )
    without_quotes = re.sub(r'"[^"]+"', " ", canonical)
    raw_tokens = _TOKEN_PATTERN.findall(without_quotes)
    terms: list[str] = []
    for raw_token in raw_tokens:
        normalized = normalize_for_search(raw_token)
        for token in normalized.split():
            if token and token not in terms:
                terms.append(token)
    normalized_query = normalize_for_search(
        " ".join([*phrases, *terms]) if phrases else canonical.replace('"', " ")
    )
    identifiers = _identifier_variants(original, normalized_query)
    compact = "".join(character for character in normalized_query if character.isalnum())
    return NormalizedQuery(
        original_query=original,
        normalized_query=normalized_query,
        phrases=phrases,
        terms=tuple(terms),
        identifier_variants=tuple(identifiers),
        contains_cjk=bool(_CJK_PATTERN.search(canonical)),
        short_query=len(compact) < 3,
    )


def normalize_for_search(value: str) -> str:
    normalized = _canonical_unicode(str(value or "")).casefold()
    normalized = "".join(
        character if character.isalnum() else " "
        for character in normalized
    )
    return " ".join(normalized.split())


def compact_identifier(value: str) -> str:
    return "".join(
        character
        for character in normalize_for_search(value)
        if character.isalnum()
    )


def _canonical_unicode(value: str) -> str:
    return unicodedata.normalize("NFKC", value).translate(_DASHES).strip()


def _identifier_variants(original: str, normalized_query: str) -> list[str]:
    variants: list[str] = []
    looks_like_identifier = (
        any(character.isdigit() for character in original)
        or "-" in _canonical_unicode(original)
        or (
            len(original.strip()) <= 12
            and any(character.isalpha() for character in original)
            and original.strip().upper() == original.strip()
        )
    )
    if not looks_like_identifier:
        return variants
    for value in (
        normalized_query,
        normalized_query.replace("-", " "),
        compact_identifier(normalized_query),
    ):
        normalized = normalize_for_search(value)
        if normalized and normalized not in variants:
            variants.append(normalized)
    canonical_original = _canonical_unicode(original)
    identifier_spans = re.findall(
        r"\b[A-Za-z]*\d+[A-Za-z]*(?:[-\s]+[A-Za-z0-9]+)+\b",
        canonical_original,
    )
    for span in identifier_spans:
        for value in (normalize_for_search(span), compact_identifier(span)):
            if value and value not in variants:
                variants.append(value)
    compact = compact_identifier(normalized_query)
    if compact and compact not in variants:
        variants.append(compact)
    compact_original = re.sub(r"[^A-Za-z0-9]", "", original)
    grouped_identifier = re.fullmatch(
        r"([A-Za-z]\d+[A-Za-z])([A-Z]{2,})",
        compact_original,
    )
    if grouped_identifier:
        grouped = normalize_for_search(
            f"{grouped_identifier.group(1)} {grouped_identifier.group(2)}"
        )
        if grouped not in variants:
            variants.append(grouped)
    return variants
