from __future__ import annotations

import typer

from app.cli_commands.shared import register_commands
from app.services.tag_service import (
    create_tag,
    list_chunk_tags,
    list_note_tags,
    list_tagged_chunks,
    list_tagged_notes,
    list_tags,
    tag_chunk,
    tag_note,
)


def create_tag_command(
    name: str = typer.Option(..., "--name"),
    tag_type: str = typer.Option(..., "--tag-type"),
    description: str | None = typer.Option(None, "--description"),
) -> None:
    """Create or update a manual knowledge tag."""
    try:
        result = create_tag(name=name, tag_type=tag_type, description=description)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    status = "created" if result.created else "updated" if result.updated else "exists"
    typer.echo(
        "Tag: "
        f"tag_id={result.tag_id}, status={status}, "
        f"name={result.name}, tag_type={result.tag_type}, description={result.description or ''}"
    )


def list_tags_command(
    tag_type: str | None = typer.Option(None, "--tag-type"),
    limit: int = typer.Option(50, "--limit", min=1),
) -> None:
    """List knowledge tags."""
    try:
        tags = list_tags(tag_type=tag_type, limit=limit)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not tags:
        typer.echo("No tags found.")
        return
    for tag in tags:
        typer.echo(
            f"tag_id={tag.tag_id}\tname={tag.name}\ttag_type={tag.tag_type}\t"
            f"description={tag.description or ''}\tcreated_at={tag.created_at}\tupdated_at={tag.updated_at}"
        )


def tag_chunk_command(
    chunk_id: int = typer.Option(..., "--chunk-id"),
    tag_id: int = typer.Option(..., "--tag-id"),
) -> None:
    """Bind a manual tag to a knowledge chunk."""
    try:
        result = tag_chunk(chunk_id=chunk_id, tag_id=tag_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    status = "created" if result.created else "exists"
    typer.echo(
        "Chunk tag: "
        f"binding_id={result.binding_id}, status={status}, "
        f"chunk_id={result.owner_id}, tag_id={result.tag_id}, "
        f"tag={result.tag_type}:{result.tag_name}"
    )


def tag_note_command(
    note_id: int = typer.Option(..., "--note-id"),
    tag_id: int = typer.Option(..., "--tag-id"),
) -> None:
    """Bind a manual tag to a personal note."""
    try:
        result = tag_note(note_id=note_id, tag_id=tag_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    status = "created" if result.created else "exists"
    typer.echo(
        "Note tag: "
        f"binding_id={result.binding_id}, status={status}, "
        f"note_id={result.owner_id}, tag_id={result.tag_id}, "
        f"tag={result.tag_type}:{result.tag_name}"
    )


def list_chunk_tags_command(chunk_id: int = typer.Option(..., "--chunk-id")) -> None:
    """List tags bound to a chunk."""
    try:
        tags = list_chunk_tags(chunk_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not tags:
        typer.echo("No chunk tags found.")
        return
    for tag in tags:
        typer.echo(f"tag_id={tag.tag_id}\tname={tag.name}\ttag_type={tag.tag_type}")


def list_note_tags_command(note_id: int = typer.Option(..., "--note-id")) -> None:
    """List tags bound to a personal note."""
    try:
        tags = list_note_tags(note_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not tags:
        typer.echo("No note tags found.")
        return
    for tag in tags:
        typer.echo(f"tag_id={tag.tag_id}\tname={tag.name}\ttag_type={tag.tag_type}")


def list_tagged_chunks_command(tag_id: int = typer.Option(..., "--tag-id")) -> None:
    """List chunks bound to a tag."""
    try:
        chunks = list_tagged_chunks(tag_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not chunks:
        typer.echo("No tagged chunks found.")
        return
    for chunk in chunks:
        typer.echo(
            f"chunk_id={chunk.chunk_id}\tdocument_title={chunk.document_title}\t"
            f"heading_path={chunk.heading_path}\tpdf_path={chunk.pdf_path or ''}\t"
            f"page={chunk.pdf_page_start or ''}"
        )


def list_tagged_notes_command(tag_id: int = typer.Option(..., "--tag-id")) -> None:
    """List personal notes bound to a tag."""
    try:
        notes = list_tagged_notes(tag_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not notes:
        typer.echo("No tagged notes found.")
        return
    for note in notes:
        typer.echo(
            f"note_id={note.note_id}\ttitle={note.note_title}\t"
            f"note_type={note.note_type}\tsource_path={note.source_path or ''}"
        )


def register_tag_commands(app: typer.Typer) -> None:
    register_commands(
        app,
        namespace="tags",
        commands=(
            ("create-tag", create_tag_command),
            ("list-tags", list_tags_command),
            ("tag-chunk", tag_chunk_command),
            ("tag-note", tag_note_command),
            ("list-chunk-tags", list_chunk_tags_command),
            ("list-note-tags", list_note_tags_command),
            ("list-tagged-chunks", list_tagged_chunks_command),
            ("list-tagged-notes", list_tagged_notes_command),
        ),
    )


__all__ = [name for name in globals() if name.endswith("_command") or name == "register_tag_commands"]
