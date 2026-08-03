from __future__ import annotations

import typer

from app.cli_commands.shared import echo_relation, register_commands
from app.services.relation_service import (
    create_relation,
    list_relations,
    list_relations_for_chunk,
    list_relations_for_note,
    list_relations_for_tag,
    show_relation,
)


def create_relation_command(
    source_type: str = typer.Option(..., "--source-type"),
    source_id: int = typer.Option(..., "--source-id"),
    relation_type: str = typer.Option(..., "--relation-type"),
    target_type: str = typer.Option(..., "--target-type"),
    target_id: int = typer.Option(..., "--target-id"),
    evidence_chunk_id: int | None = typer.Option(None, "--evidence-chunk-id"),
    note_id: int | None = typer.Option(None, "--note-id"),
    confidence: float | None = typer.Option(None, "--confidence"),
    description: str | None = typer.Option(None, "--description"),
) -> None:
    """Create or reuse a manual knowledge relation."""
    try:
        result = create_relation(
            source_type=source_type,
            source_id=source_id,
            relation_type=relation_type,
            target_type=target_type,
            target_id=target_id,
            evidence_chunk_id=evidence_chunk_id,
            note_id=note_id,
            confidence=confidence,
            description=description,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    status = "created" if result.created else "exists"
    typer.echo(
        "Relation: "
        f"relation_id={result.relation_id}, status={status}, "
        f"{result.source_type}:{result.source_id} {result.relation_type} "
        f"{result.target_type}:{result.target_id}, evidence_chunk_id={result.evidence_chunk_id or ''}"
    )


def list_relations_command(
    source_type: str | None = typer.Option(None, "--source-type"),
    source_id: int | None = typer.Option(None, "--source-id"),
    relation_type: str | None = typer.Option(None, "--relation-type"),
    target_type: str | None = typer.Option(None, "--target-type"),
    target_id: int | None = typer.Option(None, "--target-id"),
    limit: int = typer.Option(50, "--limit", min=1),
) -> None:
    """List knowledge relations."""
    try:
        relations = list_relations(
            source_type=source_type,
            source_id=source_id,
            relation_type=relation_type,
            target_type=target_type,
            target_id=target_id,
            limit=limit,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not relations:
        typer.echo("No relations found.")
        return
    for relation in relations:
        echo_relation(relation)


def show_relation_command(relation_id: int = typer.Option(..., "--relation-id")) -> None:
    """Show one knowledge relation with evidence details."""
    try:
        relation = show_relation(relation_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    echo_relation(relation)


def list_relations_for_tag_command(tag_id: int = typer.Option(..., "--tag-id")) -> None:
    try:
        relations = list_relations_for_tag(tag_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not relations:
        typer.echo("No relations found.")
        return
    for relation in relations:
        echo_relation(relation)


def list_relations_for_note_command(note_id: int = typer.Option(..., "--note-id")) -> None:
    try:
        relations = list_relations_for_note(note_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not relations:
        typer.echo("No relations found.")
        return
    for relation in relations:
        echo_relation(relation)


def list_relations_for_chunk_command(chunk_id: int = typer.Option(..., "--chunk-id")) -> None:
    try:
        relations = list_relations_for_chunk(chunk_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not relations:
        typer.echo("No relations found.")
        return
    for relation in relations:
        echo_relation(relation)


def register_relation_commands(app: typer.Typer) -> None:
    register_commands(
        app,
        namespace="relations",
        commands=(
            ("create-relation", create_relation_command),
            ("list-relations", list_relations_command),
            ("show-relation", show_relation_command),
            ("list-relations-for-tag", list_relations_for_tag_command),
            ("list-relations-for-note", list_relations_for_note_command),
            ("list-relations-for-chunk", list_relations_for_chunk_command),
        ),
    )


__all__ = [
    name
    for name in globals()
    if name.endswith("_command") or name == "register_relation_commands"
]
