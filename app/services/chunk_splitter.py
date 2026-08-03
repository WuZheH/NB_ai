from __future__ import annotations

from dataclasses import dataclass

from app.services.markdown_parser import ParsedMarkdownNode


DEFAULT_CHUNK_SIZE = 500
DEFAULT_OVERLAP = 50
SHORT_NODE_THRESHOLD = 700


@dataclass(frozen=True)
class TextChunk:
    node_order_index: int
    chunk_index: int
    heading_path: str
    chunk_text: str
    char_count: int
    token_count: int | None
    overlap_before: str | None
    overlap_after: str | None
    pdf_page_start: int | None
    pdf_page_end: int | None
    pdf_path: str | None


def split_node(
    node: ParsedMarkdownNode,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    short_node_threshold: int = SHORT_NODE_THRESHOLD,
) -> list[TextChunk]:
    text = node.raw_content.strip()
    if not text:
        return []

    if len(text) <= short_node_threshold:
        return [
            TextChunk(
                node_order_index=node.order_index,
                chunk_index=0,
                heading_path=node.heading_path,
                chunk_text=text,
                char_count=len(text),
                token_count=None,
                overlap_before=None,
                overlap_after=None,
                pdf_page_start=node.pdf_page_start,
                pdf_page_end=node.pdf_page_start,
                pdf_path=node.pdf_path,
            )
        ]

    chunks: list[TextChunk] = []
    start = 0
    chunk_index = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk_text = text[start:end].strip()
        if chunk_text:
            overlap_before = text[max(0, start - overlap) : start] or None
            overlap_after = text[end : min(len(text), end + overlap)] or None
            chunks.append(
                TextChunk(
                    node_order_index=node.order_index,
                    chunk_index=chunk_index,
                    heading_path=node.heading_path,
                    chunk_text=chunk_text,
                    char_count=len(chunk_text),
                    token_count=None,
                    overlap_before=overlap_before,
                    overlap_after=overlap_after,
                    pdf_page_start=node.pdf_page_start,
                    pdf_page_end=node.pdf_page_start,
                    pdf_path=node.pdf_path,
                )
            )
            chunk_index += 1

        if end >= len(text):
            break
        start = max(end - overlap, start + 1)

    return chunks


def split_nodes(nodes: list[ParsedMarkdownNode]) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for node in nodes:
        chunks.extend(split_node(node))
    return chunks

