from __future__ import annotations

import typer

from app.cli_commands.shared import register_commands
from app.services.library_service import (
    get_library_home,
    list_read_books,
    search_library,
    show_library_chunk,
    show_library_document,
    show_library_evidence,
    show_library_note,
    show_library_notes,
)


def library_home_command(
    item_type: str | None = typer.Option(None, "--item-type"),
    document_type: str | None = typer.Option(None, "--document-type"),
    research_direction: str | None = typer.Option(None, "--research-direction"),
    limit: int = typer.Option(20, "--limit", min=1),
    top_k: int | None = typer.Option(None, "--top-k", min=1),
) -> None:
    """Show read shelf home cards from the core read library."""
    try:
        items = get_library_home(
            item_type=item_type,
            document_type=document_type,
            research_direction=research_direction,
            limit=top_k if top_k is not None else limit,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Read Shelf")
    if not items:
        typer.echo("  []")
        return
    for item in items:
        if item.item_type == "document":
            typer.echo(
                f"[document] id={item.item_id} title={item.title} "
                f"document_type={item.document_type or ''} read_status={item.read_status or ''} "
                f"research_direction={item.research_direction or ''} updated_at={item.updated_at} "
                f"has_pdf={item.has_pdf} has_zotero={item.has_zotero} "
                f"chunks={item.chunk_count} notes={item.note_count} tags={item.tag_count}"
            )
        else:
            typer.echo(
                f"[note] id={item.item_id} title={item.title} "
                f"note_type={item.note_type or ''} source_document_id={item.source_document_id or ''} "
                f"research_direction={item.research_direction or ''} updated_at={item.updated_at} "
                f"has_pdf={item.has_pdf} has_zotero={item.has_zotero} "
                f"linked_chunks={item.chunk_count} tags={item.tag_count}"
            )


def library_search_command(
    query: str,
    limit: int = typer.Option(10, "--limit", min=1),
    top_k: int | None = typer.Option(None, "--top-k", min=1),
) -> None:
    """Search the core read library across documents, chunks, notes, tags, and relations."""
    try:
        results = search_library(query=query, limit=limit, top_k=top_k)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Library Search Results: {query}")
    if not results:
        typer.echo("  []")
        return
    grouped: dict[str, list[object]] = {}
    for result in results:
        grouped.setdefault(result.result_type, []).append(result)
    for source_type in ["chunk_result", "note_result", "document_result", "tag_result", "relation_result"]:
        items = grouped.get(source_type, [])
        if not items:
            continue
        typer.echo(f"{source_type}s:")
        for result in items:
            typer.echo(
                f"  result_type={result.result_type}\tid={result.id}\ttitle={result.title}\t"
                f"document_id={result.document_id if result.document_id is not None else ''}\t"
                f"document_title={result.document_title or ''}\t"
                f"document_type={result.document_type or ''}\tnote_type={result.note_type or ''}\t"
                f"heading_path={result.heading_path or ''}\t"
                f"pdf_path={result.pdf_path or '暂无 PDF 路径。'}\t"
                f"pdf_page_start={result.pdf_page_start if result.pdf_page_start is not None else ''}\t"
                f"pdf_open_url={result.pdf_open_url or '暂无 PDF 路径。'}\t"
                f"zotero_open_url={result.zotero_open_url or '暂无 Zotero 关联。'}"
            )
            typer.echo(f"    snippet={result.snippet or '暂无个人总结。'}")
            typer.echo(
                "    related_notes="
                + (", ".join(result.related_notes) if result.related_notes else "[]")
            )
            typer.echo("    tags=" + (", ".join(result.tags) if result.tags else "[]"))
            typer.echo(
                "    related_relations="
                + (
                    ", ".join(str(relation.relation_id) for relation in result.related_relations)
                    if result.related_relations
                    else "[]"
                )
            )
            if result.relation_summary:
                typer.echo(f"    relation_summary={result.relation_summary}")


def library_show_document_command(document_id: int = typer.Option(..., "--document-id")) -> None:
    """Show a read library document preview."""
    try:
        document = show_library_document(document_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"document_id={document.document_id}")
    typer.echo(f"title={document.title}")
    typer.echo(f"document_type={document.document_type}")
    typer.echo(f"content_layer={document.content_layer}")
    typer.echo(f"read_status={document.read_status}")
    typer.echo(f"research_direction={document.research_direction or ''}")
    typer.echo(f"source_path={document.source_path or ''}")
    typer.echo(f"pdf_path={document.pdf_path or '暂无 PDF 路径。'}")
    typer.echo(f"pdf_open_url={document.pdf_open_url or '暂无 PDF 路径。'}")
    typer.echo(f"zotero_key={document.zotero_key or '暂无 Zotero 关联。'}")
    typer.echo(f"zotero_open_url={document.zotero_open_url or '暂无 Zotero 关联。'}")
    typer.echo(f"created_at={document.created_at}")
    typer.echo(f"updated_at={document.updated_at}")
    typer.echo(f"chunk_count={document.chunk_count}")
    typer.echo(f"note_count={document.note_count}")
    typer.echo("tags=" + (", ".join(document.tags) if document.tags else "[]"))
    typer.echo("top_headings:")
    if not document.top_headings:
        typer.echo("  []")
    for heading in document.top_headings:
        typer.echo(f"  - {heading}")
    typer.echo("related_notes:")
    if not document.related_notes:
        typer.echo("  []")
    for note in document.related_notes:
        typer.echo(f"  note_id={note.note_id}\ttitle={note.title}\tnote_type={note.note_type}")
        typer.echo(f"    summary={note.summary or '暂无个人总结。'}")
    _echo_library_relations(document.related_relations)


def library_show_note_command(note_id: int = typer.Option(..., "--note-id")) -> None:
    """Show a personal note preview from the read library."""
    try:
        note = show_library_note(note_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"note_id={note.note_id}")
    typer.echo(f"title={note.title}")
    typer.echo(f"note_type={note.note_type}")
    typer.echo(f"summary={note.summary or '暂无个人总结。'}")
    typer.echo(f"source_path={note.source_path or ''}")
    typer.echo(f"document_id={note.document_id or ''}")
    typer.echo(f"scope_type={note.scope_type or ''}")
    typer.echo(f"scope_path={note.scope_path or ''}")
    typer.echo(f"snippet={note.snippet}")
    typer.echo("note_tags=" + (", ".join(note.note_tags) if note.note_tags else "[]"))
    typer.echo("linked_chunks:")
    if not note.linked_chunks:
        typer.echo("  []")
    for chunk in note.linked_chunks:
        typer.echo(
            f"  chunk_id={chunk.chunk_id}\tdocument_title={chunk.document_title}\t"
            f"heading_path={chunk.heading_path}\tpdf_path={chunk.pdf_path or '暂无 PDF 路径。'}\t"
            f"pdf_page_start={chunk.pdf_page_start if chunk.pdf_page_start is not None else ''}\t"
            f"pdf_open_url={chunk.pdf_open_url or '暂无 PDF 路径。'}"
        )
        typer.echo(f"    snippet={chunk.snippet}")
    _echo_library_relations(note.related_relations)


def library_show_chunk_command(chunk_id: int = typer.Option(..., "--chunk-id")) -> None:
    """Show an evidence chunk preview from the read library."""
    try:
        chunk = show_library_chunk(chunk_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"chunk_id={chunk.chunk_id}")
    typer.echo(f"document_id={chunk.document_id}")
    typer.echo(f"document_title={chunk.document_title}")
    typer.echo(f"document_type={chunk.document_type}")
    typer.echo(f"heading_path={chunk.heading_path}")
    typer.echo(f"snippet={chunk.snippet}")
    typer.echo(f"pdf_path={chunk.pdf_path or '暂无 PDF 路径。'}")
    typer.echo(f"pdf_page_start={chunk.pdf_page_start if chunk.pdf_page_start is not None else ''}")
    typer.echo(f"pdf_page_end={chunk.pdf_page_end if chunk.pdf_page_end is not None else ''}")
    typer.echo(f"pdf_open_url={chunk.pdf_open_url or '暂无 PDF 路径。'}")
    typer.echo(f"zotero_open_url={chunk.zotero_open_url or '暂无 Zotero 关联。'}")
    typer.echo("chunk_tags=" + (", ".join(chunk.chunk_tags) if chunk.chunk_tags else "[]"))
    typer.echo("related_notes:")
    if not chunk.related_notes:
        typer.echo("  []")
    for note in chunk.related_notes:
        typer.echo(f"  note_id={note.note_id}\ttitle={note.title}\tnote_type={note.note_type}")
        typer.echo(f"    summary={note.summary or '暂无个人总结。'}")
    _echo_library_relations(chunk.related_relations)


def list_read_books_command(limit: int = typer.Option(100, "--limit", min=1)) -> None:
    """List read/mastered book and chapter documents."""
    items = list_read_books(limit=limit)
    if not items:
        typer.echo("No read books or chapters found.")
        return
    for item in items:
        typer.echo(
            f"document_id={item.document_id}\t"
            f"title={item.title}\t"
            f"document_type={item.document_type}\t"
            f"read_status={item.read_status}\t"
            f"research_direction={item.research_direction or ''}\t"
            f"pdf_path={item.pdf_path or '暂无 PDF 路径。'}\t"
            f"zotero_key={item.zotero_key or '暂无 Zotero 关联。'}\t"
            f"chunk_count={item.chunk_count}\t"
            f"note_count={item.note_count}"
        )


def show_library_document_command(document_id: int = typer.Option(..., "--document-id")) -> None:
    """Show metadata and counts for one read library document."""
    try:
        item = show_library_document(document_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"document_id={item.document_id}")
    typer.echo(f"title={item.title}")
    typer.echo(f"document_type={item.document_type}")
    typer.echo(f"content_layer={item.content_layer}")
    typer.echo(f"read_status={item.read_status}")
    typer.echo(f"research_direction={item.research_direction or ''}")
    typer.echo(f"source_path={item.source_path or ''}")
    typer.echo(f"pdf_path={item.pdf_path or '暂无 PDF 路径。'}")
    typer.echo(f"zotero_key={item.zotero_key or '暂无 Zotero 关联。'}")
    typer.echo(f"created_at={item.created_at}")
    typer.echo(f"updated_at={item.updated_at}")
    typer.echo(f"chunk_count={item.chunk_count}")
    typer.echo(f"note_count={item.note_count}")
    typer.echo("tags=" + (", ".join(item.tags) if item.tags else "[]"))


def show_library_notes_command(document_id: int = typer.Option(..., "--document-id")) -> None:
    """List personal understanding notes associated with a read library document."""
    try:
        notes = show_library_notes(document_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not notes:
        typer.echo("No library notes found.")
        return
    for note in notes:
        typer.echo(
            f"note_id={note.note_id}\t"
            f"title={note.title}\t"
            f"note_type={note.note_type}\t"
            f"source_path={note.source_path or ''}\t"
            f"scope_type={note.scope_type or ''}\t"
            f"scope_path={note.scope_path or ''}\t"
            f"note_tags={', '.join(note.note_tags) if note.note_tags else '[]'}"
        )
        typer.echo(f"    summary={note.summary or '暂无个人总结。'}")
        typer.echo(f"    content_snippet={note.content_snippet}")


def show_library_evidence_command(
    document_id: int = typer.Option(..., "--document-id"),
    limit: int = typer.Option(20, "--limit", min=1),
) -> None:
    """List evidence chunks for one read library document."""
    try:
        chunks = show_library_evidence(document_id, limit=limit)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not chunks:
        typer.echo("No library evidence chunks found.")
        return
    for chunk in chunks:
        typer.echo(
            f"chunk_id={chunk.chunk_id}\t"
            f"heading_path={chunk.heading_path}\t"
            f"pdf_page_start={chunk.pdf_page_start if chunk.pdf_page_start is not None else ''}\t"
            f"pdf_page_end={chunk.pdf_page_end if chunk.pdf_page_end is not None else ''}\t"
            f"pdf_open_url={chunk.pdf_open_url or '暂无 PDF 路径。'}\t"
            f"related_note_titles={', '.join(chunk.related_note_titles) if chunk.related_note_titles else '[]'}\t"
            f"chunk_tags={', '.join(chunk.chunk_tags) if chunk.chunk_tags else '[]'}"
        )
        typer.echo(f"    snippet={chunk.snippet}")


def _echo_library_relations(relations: list[object]) -> None:
    typer.echo("related_relations:")
    if not relations:
        typer.echo("  []")
        return
    for relation in relations:
        typer.echo(
            f"  relation_id={getattr(relation, 'relation_id')}\t"
            f"source={getattr(relation, 'source_type')}:{getattr(relation, 'source_id')}\t"
            f"relation_type={getattr(relation, 'relation_type')}\t"
            f"target={getattr(relation, 'target_type')}:{getattr(relation, 'target_id')}\t"
            f"evidence_chunk_id={getattr(relation, 'evidence_chunk_id') or ''}\t"
            f"note_id={getattr(relation, 'note_id') or ''}\t"
            f"confidence={getattr(relation, 'confidence') if getattr(relation, 'confidence') is not None else ''}"
        )
        description = getattr(relation, "description")
        if description:
            typer.echo(f"    description={description}")


def register_library_commands(app: typer.Typer) -> None:
    register_commands(
        app,
        namespace="library",
        commands=(
            ("library-home", library_home_command),
            ("library-search", library_search_command),
            ("library-show-document", library_show_document_command),
            ("library-show-note", library_show_note_command),
            ("library-show-chunk", library_show_chunk_command),
            ("list-read-books", list_read_books_command),
            ("show-library-document", show_library_document_command),
            ("show-library-notes", show_library_notes_command),
            ("show-library-evidence", show_library_evidence_command),
        ),
    )


__all__ = [
    name
    for name in globals()
    if name.endswith("_command") or name == "register_library_commands"
]
