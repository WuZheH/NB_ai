from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
PDF_PAGE_RE = re.compile(r"<!--\s*PDF_PAGE:\s*(\d+)\s*-->")
PDF_PATH_RE = re.compile(r"<!--\s*PDF_PATH:\s*(.*?)\s*-->")


@dataclass(frozen=True)
class ParsedMarkdownNode:
    order_index: int
    parent_order_index: int | None
    heading_level: int
    heading_title: str
    heading_path: str
    raw_content: str
    pdf_page_start: int | None
    pdf_path: str | None


@dataclass(frozen=True)
class ParsedMarkdown:
    source_path: str
    title: str
    pdf_path: str | None
    nodes: list[ParsedMarkdownNode]


def parse_markdown_file(path: str | Path) -> ParsedMarkdown:
    markdown_path = Path(path)
    text = markdown_path.read_text(encoding="utf-8")
    return parse_markdown(text, source_path=str(markdown_path))


def parse_markdown(text: str, source_path: str = "") -> ParsedMarkdown:
    nodes: list[ParsedMarkdownNode] = []
    heading_stack: dict[int, tuple[int, str]] = {}
    current_heading: dict[str, object] | None = None
    current_lines: list[str] = []
    current_pdf_page: int | None = None
    document_pdf_path: str | None = None
    pending_pdf_page: int | None = None

    def has_current_content() -> bool:
        return any(line.strip() for line in current_lines)

    def flush_current() -> None:
        nonlocal current_heading, current_lines
        if current_heading is None:
            current_lines = []
            return

        level = int(current_heading["level"])
        order_index = int(current_heading["order_index"])
        parent_order_index = current_heading["parent_order_index"]
        title = str(current_heading["title"])
        heading_path = str(current_heading["heading_path"])
        pdf_page_start = current_heading["pdf_page_start"]

        nodes.append(
            ParsedMarkdownNode(
                order_index=order_index,
                parent_order_index=parent_order_index if isinstance(parent_order_index, int) else None,
                heading_level=level,
                heading_title=title,
                heading_path=heading_path,
                raw_content="\n".join(current_lines).strip(),
                pdf_page_start=pdf_page_start if isinstance(pdf_page_start, int) else None,
                pdf_path=document_pdf_path,
            )
        )
        current_lines = []

    def start_page_continuation(page_number: int) -> None:
        nonlocal current_heading, pending_pdf_page
        if current_heading is None:
            pending_pdf_page = page_number
            return

        if not has_current_content():
            current_heading["pdf_page_start"] = page_number
            pending_pdf_page = page_number
            return

        flush_current()
        level = int(current_heading["level"])
        continuation_order_index = len(nodes)
        current_heading = {
            "level": level,
            "order_index": continuation_order_index,
            "parent_order_index": current_heading["parent_order_index"],
            "title": current_heading["title"],
            "heading_path": current_heading["heading_path"],
            "pdf_page_start": page_number,
        }
        heading_stack[level] = (continuation_order_index, str(current_heading["title"]))
        pending_pdf_page = None

    for line in text.splitlines():
        path_match = PDF_PATH_RE.search(line)
        if path_match:
            document_pdf_path = path_match.group(1).strip() or None
            continue

        page_match = PDF_PAGE_RE.search(line)
        if page_match:
            current_pdf_page = int(page_match.group(1))
            start_page_continuation(current_pdf_page)
            continue

        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush_current()

            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            for stale_level in [key for key in heading_stack if key >= level]:
                del heading_stack[stale_level]

            parent_level = max((key for key in heading_stack if key < level), default=None)
            parent_order_index = heading_stack[parent_level][0] if parent_level is not None else None
            heading_path_parts = [heading_stack[key][1] for key in sorted(heading_stack) if key < level]
            heading_path_parts.append(title)
            heading_path = " / ".join(heading_path_parts)
            order_index = len(nodes)
            heading_stack[level] = (order_index, title)

            current_heading = {
                "level": level,
                "order_index": order_index,
                "parent_order_index": parent_order_index,
                "title": title,
                "heading_path": heading_path,
                "pdf_page_start": pending_pdf_page if pending_pdf_page is not None else current_pdf_page,
            }
            pending_pdf_page = None
            continue

        if current_heading is not None:
            current_lines.append(line)

    flush_current()

    title = nodes[0].heading_title if nodes else Path(source_path).stem
    return ParsedMarkdown(
        source_path=source_path,
        title=title,
        pdf_path=document_pdf_path,
        nodes=nodes,
    )
