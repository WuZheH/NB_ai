from __future__ import annotations

import re
from typing import Any


CONTEXT_WINDOW = 1
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "for",
    "from",
    "in",
    "is",
    "of",
    "or",
    "the",
    "to",
    "using",
    "with",
}

EXPANSION_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "mfa_advantage_cn",
        "markers": ("混合因子分析器", "因子分析器", "mfa", "mixture of factor"),
        "terms": (
            "mixture of factor analyzers",
            "mixture of PPCA",
            "low-rank covariance",
            "low-rank version",
            "full covariance GMM",
            "parameter count",
            "O(KLD)",
            "O(KD^2)",
            "O(KD2)",
            "overfitting",
            "factor analyzers",
        ),
        "scope_anchor_terms": (
            "mixture of factor analyzers",
            "mixture of PPCA",
            "factor analyzers",
        ),
    },
    {
        "id": "regularization_cn_en",
        "markers": ("正则化", "regularization", "regularisation", "weight decay"),
        "terms": (
            "regularization",
            "regularisation",
            "weight decay",
            "overfitting",
            "generalization",
            "penalty",
            "prior",
        ),
        "scope_anchor_terms": ("regularization", "regularisation", "weight decay"),
    },
    {
        "id": "optimization_cn_en",
        "markers": ("optimization", "优化", "梯度", "牛顿", "黑塞", "海森"),
        "terms": (
            "optimization",
            "gradient descent",
            "newton",
            "newton's method",
            "hessian",
            "line search",
            "overfit",
        ),
        "scope_anchor_terms": ("optimization", "gradient descent", "newton", "hessian", "line search"),
    },
    {
        "id": "tice_mice_hierarchy",
        "markers": ("tice", "mice", "分层筛选", "层次筛选"),
        "terms": (
            "TICe",
            "MICe",
            "hierarchical screening",
            "tiered screening",
            "two-stage screening",
            "instance complexity",
            "training instance complexity",
            "model instance complexity",
        ),
        "scope_anchor_terms": ("TICe", "MICe"),
        "scope_anchor_policy": "all",
    },
    {
        "id": "object_note_neighbor",
        "markers": ("对象", "object", "笔记", "note", "zotero"),
        "terms": ("object", "candidate", "note", "zotero", "annotation"),
    },
    {
        "id": "robust_motion_diffusion",
        "markers": ("鲁棒", "扩散", "运动", "motion", "diffusion", "residual"),
        "terms": ("robust", "robustness", "diffusion", "motion", "residual", "unknown downsampling"),
    },
)


def expand_query(query: str) -> dict[str, Any]:
    clean_query = " ".join(str(query or "").split())
    base_terms = _base_terms(clean_query)
    expanded_terms: list[str] = []
    expansion_rules: list[str] = []
    scope_anchor_terms: list[str] = []
    scope_anchor_groups: list[dict[str, Any]] = []
    query_lc = clean_query.casefold()
    for rule in EXPANSION_RULES:
        if any(str(marker).casefold() in query_lc for marker in rule["markers"]):
            expansion_rules.append(str(rule["id"]))
            expanded_terms.extend(str(term) for term in rule["terms"])
            anchors = _unique([str(term) for term in rule.get("scope_anchor_terms", ())])
            scope_anchor_terms.extend(anchors)
            if anchors:
                scope_anchor_groups.append(
                    {
                        "rule_id": str(rule["id"]),
                        "terms": [term.casefold() for term in anchors],
                        "match_policy": str(rule.get("scope_anchor_policy") or "any"),
                    }
                )
    all_terms = _unique([*base_terms, *expanded_terms])
    return {
        "original_query": clean_query,
        "terms": [term.casefold() for term in all_terms],
        "expanded_terms": _unique(expanded_terms),
        "expansion_rules": expansion_rules,
        "scope_anchor_terms": [term.casefold() for term in _unique(scope_anchor_terms)],
        "scope_anchor_groups": scope_anchor_groups,
        "context_window": CONTEXT_WINDOW,
        "neighbor_expansion": {
            "nearby_chunks": True,
            "same_chapter_chunks": True,
            "object_linked_chunks": True,
            "note_linked_chunks": True,
        },
        "warnings": [],
    }


def _base_terms(query: str) -> list[str]:
    terms = [query.casefold()]
    for token in re.findall(r"[a-zA-Z0-9_+.^-]+", query.casefold()):
        if len(token) >= 2 and token not in STOPWORDS:
            terms.append(token)
    for marker in ("牛顿", "梯度", "正则化", "对象", "机制", "鲁棒", "残差", "优化"):
        if marker in query:
            terms.append(marker)
    return _unique(terms)


def _unique(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        clean = " ".join(str(value or "").split())
        key = clean.casefold()
        if clean and key not in {item.casefold() for item in unique}:
            unique.append(clean)
    return unique
