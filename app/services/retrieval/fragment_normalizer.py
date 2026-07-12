from __future__ import annotations

import hashlib
import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping


_BLOCK_TAGS = {
    "address",
    "blockquote",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "p",
    "pre",
    "table",
    "td",
    "th",
    "tr",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS or tag == "br":
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def normalize_text(value: Any, *, preserve_paragraphs: bool = True) -> str:
    text = str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[\t \f\v]+", " ", line).strip() for line in text.split("\n")]
    if not preserve_paragraphs:
        return " ".join(part for part in lines if part).strip()
    result: list[str] = []
    blank = False
    for line in lines:
        if line:
            result.append(line)
            blank = False
        elif result and not blank:
            result.append("")
            blank = True
    return "\n".join(result).strip()


def html_to_text(value: Any) -> str:
    raw = str(value or "")
    if "<" not in raw:
        return normalize_text(html.unescape(raw))
    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    return normalize_text("".join(parser.parts))


def split_text_blocks(value: Any) -> list[str]:
    text = html_to_text(value)
    if not text:
        return []
    return [block.strip() for block in re.split(r"\n\s*\n+", text) if block.strip()]


def sha256_text(value: Any) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def parse_json(value: Any, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def parse_heading_path(value: Any) -> list[str]:
    parsed = parse_json(value)
    if isinstance(parsed, list):
        return _unique_strings(parsed)
    if isinstance(parsed, str):
        value = parsed
    text = normalize_text(value, preserve_paragraphs=False)
    if not text:
        return []
    for separator in (" > ", " / ", "::", "\n"):
        if separator in text:
            return _unique_strings(text.split(separator))
    return [text]


def normalize_string_list(value: Any) -> list[str]:
    parsed = parse_json(value, value)
    if isinstance(parsed, Mapping):
        parsed = list(parsed.values())
    if isinstance(parsed, str):
        parsed = re.split(r"[,;\n]", parsed)
    if not isinstance(parsed, Iterable):
        return []
    return _unique_strings(parsed)


def build_index_text(*values: Any) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidates = value if isinstance(value, (list, tuple, set)) else [value]
        for candidate in candidates:
            text = normalize_text(candidate, preserve_paragraphs=False)
            marker = text.casefold()
            if text and marker not in seen:
                seen.add(marker)
                parts.append(text)
    return "\n".join(parts)


def infer_language(value: str) -> str | None:
    text = normalize_text(value, preserve_paragraphs=False)
    if not text:
        return None
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if cjk and latin:
        return "mixed"
    if cjk:
        return "zh"
    if latin:
        return "en"
    return "und"


def int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def year_or_none(value: Any) -> int | None:
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return int(match.group(0)) if match else None


def page_number_from_position(position: Mapping[str, Any] | None) -> int | None:
    if not position:
        return None
    page_index = int_or_none(position.get("pageIndex"))
    return page_index + 1 if page_index is not None and page_index >= 0 else None


def bbox_from_position(position: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not position:
        return None
    payload = {key: position[key] for key in ("pageIndex", "rects", "paths") if key in position}
    return payload or None


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = normalize_text(value, preserve_paragraphs=False)
        marker = cleaned.casefold()
        if cleaned and marker not in seen:
            seen.add(marker)
            result.append(cleaned)
    return result
