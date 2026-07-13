from __future__ import annotations

import typer

from app.cli_commands.shared import register_commands
from app.services.personal_note_service import (
    import_note_md,
    link_note_to_chunk,
    list_chunk_notes,
    list_note_evidence,
    list_personal_notes,
    show_personal_note,
)


def import_note_command(
    path: str,
    note_type: str = typer.Option(..., "--note-type"),
    document_id: int | None = typer.Option(None, "--document-id"),
    scope_type: str | None = typer.Option(None, "--scope-type"),
    scope_path: str | None = typer.Option(None, "--scope-path"),
) -> None:
    """Import a user-written Markdown note into personal_notes."""
    try:
        result = import_note_md(
            path=path,
            note_type=note_type,
            document_id=document_id,
            scope_type=scope_type,
            scope_path=scope_path,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    status = "created" if result.created else "updated"
    typer.echo(
        "Imported note: "
        f"note_id={result.note_id}, status={status}, "
        f"note_type={result.note_type}, title={result.title}, "
        f"source_path={result.source_path or ''}"
    )


def list_notes_command(
    note_type: str | None = typer.Option(None, "--note-type"),
    limit: int = typer.Option(20, "--limit", min=1),
) -> None:
    """List personal notes."""
    try:
        notes = list_personal_notes(note_type=note_type, limit=limit)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not notes:
        typer.echo("No personal notes found.")
        return

    for note in notes:
        typer.echo(
            f"note_id={note.note_id}\ttype={note.note_type}\t"
            f"title={note.title}\tscope_path={note.scope_path or ''}\t"
            f"source_path={note.source_path or ''}\t"
            f"created_at={note.created_at}\tupdated_at={note.updated_at}"
        )


def show_note_command(note_id: int) -> None:
    """Show a personal note without printing long content."""
    try:
        note = show_personal_note(note_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"note_id={note.note_id}")
    typer.echo(f"title={note.title}")
    typer.echo(f"note_type={note.note_type}")
    typer.echo(f"source_path={note.source_path or ''}")
    typer.echo(f"summary={note.summary or ''}")
    typer.echo(f"content_snippet={note.content_snippet}")


def link_note_command(
    note_id: int = typer.Option(..., "--note-id"),
    chunk_id: int = typer.Option(..., "--chunk-id"),
    link_type: str = typer.Option(..., "--link-type"),
    evidence_role: str = typer.Option(..., "--evidence-role"),
    quote_text: str | None = typer.Option(None, "--quote-text"),
    confidence: float | None = typer.Option(None, "--confidence"),
) -> None:
    """Manually link a personal note to an evidence chunk."""
    try:
        result = link_note_to_chunk(
            note_id=note_id,
            chunk_id=chunk_id,
            link_type=link_type,
            evidence_role=evidence_role,
            quote_text=quote_text,
            confidence=confidence,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    status = "created" if result.created else "exists"
    typer.echo(
        "Linked note evidence: "
        f"link_id={result.link_id}, status={status}, "
        f"note_id={result.note_id}, chunk_id={result.chunk_id}, "
        f"link_type={result.link_type}, evidence_role={result.evidence_role}"
    )


def list_note_evidence_command(note_id: int = typer.Option(..., "--note-id")) -> None:
    """List evidence chunks linked to a personal note."""
    try:
        items = list_note_evidence(note_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not items:
        typer.echo("No evidence links found.")
        return

    for item in items:
        typer.echo(
            f"chunk_id={item.chunk_id}\tdocument_title={item.document_title}\t"
            f"heading_path={item.heading_path}\tpdf_path={item.pdf_path or ''}\t"
            f"page={item.pdf_page_start or ''}\tpdf_open_url={item.pdf_open_url or ''}\t"
            f"link_type={item.link_type}\tevidence_role={item.evidence_role}\t"
            f"quote={item.quote_text_snippet or ''}"
        )


def list_chunk_notes_command(chunk_id: int = typer.Option(..., "--chunk-id")) -> None:
    """List personal notes linked to an evidence chunk."""
    try:
        items = list_chunk_notes(chunk_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not items:
        typer.echo("No linked notes found.")
        return

    for item in items:
        typer.echo(
            f"note_id={item.note_id}\ttitle={item.note_title}\t"
            f"note_type={item.note_type}\tlink_type={item.link_type}\t"
            f"evidence_role={item.evidence_role}"
        )


def register_note_commands(app: typer.Typer) -> None:
    register_commands(
        app,
        namespace="notes",
        commands=(
            ("import-note", import_note_command),
            ("list-notes", list_notes_command),
            ("show-note", show_note_command),
            ("link-note", link_note_command),
            ("list-note-evidence", list_note_evidence_command),
            ("list-chunk-notes", list_chunk_notes_command),
        ),
    )


__all__ = [
    "import_note_command",
    "link_note_command",
    "list_chunk_notes_command",
    "list_note_evidence_command",
    "list_notes_command",
    "register_note_commands",
    "show_note_command",
]
