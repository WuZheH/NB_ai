from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from app.core.paths import RUNTIME_PROJECT_ROOT, RUNTIME_STATE_DIR


PROJECT_ROOT = RUNTIME_PROJECT_ROOT
DEFAULT_MARKER_ARTIFACT_ROOT = RUNTIME_STATE_DIR / "ocr_backend_benchmark"


def load_marker_structure_hints(
    *,
    page_start: int,
    page_end: int,
    artifact_path: str | Path | None = None,
    search_roots: list[str | Path] | None = None,
) -> dict[str, Any]:
    """Read already generated Marker output as optional structure hints only."""
    artifact = _find_artifact(page_start, artifact_path=artifact_path, search_roots=search_roots)
    base: dict[str, Any] = {
        "marker_structure_available": False,
        "artifact_path": str(artifact) if artifact else None,
        "artifact_source": "existing_artifact" if artifact else "not_found",
        "marker_inference_run": False,
        "marker_cache_used": False,
        "marker_cache_mode": "not_used",
        "page_start": page_start,
        "page_end": page_end,
        "page_markers": [],
        "page_marker_remapped": False,
        "headings": [],
        "formula_blocks": [],
        "candidate_paragraph_breaks": [],
        "marker_rejected_repetition": False,
        "marker_rejected_repetition_count": 0,
        "repetition_samples": [],
    }
    if artifact is None:
        return base
    try:
        markdown = _read_artifact_as_markdown(artifact)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {**base, "artifact_error": f"{type(exc).__name__}: {exc}"}

    selected, page_markers, remapped = _select_page_markdown(
        markdown,
        page_start=page_start,
        page_end=page_end,
        artifact=artifact,
    )
    headings = _extract_headings(selected)
    formulas = _extract_formula_blocks(selected)
    paragraphs = _extract_paragraph_breaks(selected)
    repetitions = _repetition_samples(selected)
    return {
        **base,
        "marker_structure_available": bool(selected.strip()),
        "page_markers": page_markers,
        "page_marker_remapped": remapped,
        "headings": headings,
        "formula_blocks": formulas,
        "candidate_paragraph_breaks": paragraphs,
        "marker_rejected_repetition": bool(repetitions),
        "marker_rejected_repetition_count": 1 if repetitions else 0,
        "repetition_samples": repetitions,
    }


def _find_artifact(
    page_start: int,
    *,
    artifact_path: str | Path | None,
    search_roots: list[str | Path] | None,
) -> Path | None:
    if artifact_path:
        candidate = Path(artifact_path)
        return candidate if candidate.exists() else None
    roots = [Path(root) for root in (search_roots or [DEFAULT_MARKER_ARTIFACT_ROOT])]
    candidates: list[Path] = []
    for root in roots:
        if root.exists():
            candidates.extend(root.rglob(f"marker_raw_page_{page_start}.md"))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def _read_artifact_as_markdown(artifact: Path) -> str:
    if artifact.suffix.casefold() == ".json":
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        return _json_page_blocks_to_markdown(payload)
    return artifact.read_text(encoding="utf-8")


def _json_page_blocks_to_markdown(payload: Any) -> str:
    pages = payload.get("pages") if isinstance(payload, dict) else None
    if not isinstance(pages, list):
        raise ValueError("Marker JSON artifact does not include pages")
    output: list[str] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        number = int(page.get("page_number") or 0)
        output.append(f"<!-- PDF_PAGE: {number} -->")
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            raw = str(block.get("html") or block.get("text") or "")
            text = re.sub(r"<[^>]+>", "", html.unescape(raw)).strip()
            if not text:
                continue
            if re.search(r"<h[1-6][\s>]", raw, flags=re.IGNORECASE):
                level_match = re.search(r"<h([1-6])", raw, flags=re.IGNORECASE)
                level = int(level_match.group(1)) if level_match else 2
                text = f"{'#' * level} {text}"
            output.append(text)
            output.append("")
    return "\n".join(output)


def _select_page_markdown(
    markdown: str,
    *,
    page_start: int,
    page_end: int,
    artifact: Path,
) -> tuple[str, list[int], bool]:
    marker_pattern = re.compile(r"<!--\s*PDF_PAGE:\s*(\d+)\s*-->")
    matches = list(marker_pattern.finditer(markdown))
    if not matches:
        return markdown, [page_start], False
    sections: dict[int, str] = {}
    for index, match in enumerate(matches):
        page = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections[page] = markdown[match.end() : end]
    requested = [page for page in range(page_start, page_end + 1) if page in sections]
    if requested:
        return "\n".join(sections[page] for page in requested), requested, False
    expected_single_page_name = re.search(rf"marker_raw_page_{page_start}\.md$", artifact.name, flags=re.IGNORECASE)
    if page_start == page_end and expected_single_page_name and len(sections) == 1:
        return next(iter(sections.values())), [page_start], True
    return "", [], False


def _extract_headings(markdown: str) -> list[dict[str, Any]]:
    return [
        {"level": len(match.group(1)), "text": match.group(2).strip()}
        for match in re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", markdown)
    ]


def _extract_formula_blocks(markdown: str) -> list[dict[str, Any]]:
    blocks = re.findall(r"\$\$.*?\$\$|\\\[.*?\\\]|(?<!\$)\$[^$\n]{3,}\$(?!\$)", markdown, flags=re.DOTALL)
    return [{"text": re.sub(r"\s+", " ", block).strip()} for block in blocks]


def _extract_paragraph_breaks(markdown: str) -> list[dict[str, Any]]:
    blocks = [re.sub(r"\s+", " ", block).strip() for block in re.split(r"\n\s*\n", markdown)]
    return [{"after_block_index": index, "sample": block[:120]} for index, block in enumerate(blocks[:-1]) if block]


def _repetition_samples(markdown: str) -> list[dict[str, Any]]:
    tokens = re.findall(r"\\pi\^\{[^}]+\}\[[^\]]+\]|\([^)\n]{1,16}\)|\$[^$\n]{3,60}\$", markdown)
    counts: dict[str, int] = {}
    for token in tokens:
        normalized = re.sub(r"\s+", "", token)
        counts[normalized] = counts.get(normalized, 0) + 1
    return [
        {"token": token, "count": count}
        for token, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if count >= 6
    ][:5]
