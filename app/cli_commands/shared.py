from __future__ import annotations

from collections.abc import Callable, Iterable

import typer


CommandCallback = Callable[..., object]


def register_commands(
    app: typer.Typer,
    *,
    namespace: str,
    commands: Iterable[tuple[str, CommandCallback]],
) -> None:
    """Register a command group once on a Typer application."""

    marker = f"_notebook_ai_cli_registered_{namespace}"
    if getattr(app, marker, False):
        return
    for name, callback in commands:
        app.command(name)(callback)
    setattr(app, marker, True)


def echo_scored_results(results: list[object], score_label: str) -> None:
    for index, result in enumerate(results, start=1):
        typer.echo(f"[{index}] {getattr(result, 'document_title')}")
        typer.echo(f"    {score_label}={getattr(result, 'score'):.4f}")
        typer.echo(
            f"    document_id={getattr(result, 'document_id')} "
            f"type={getattr(result, 'document_type')} layer={getattr(result, 'content_layer')}"
        )
        typer.echo(f"    heading_path={getattr(result, 'heading_path')}")
        typer.echo(f"    chunk_id={getattr(result, 'chunk_id')}")
        typer.echo(f"    snippet={getattr(result, 'chunk_text_snippet')}")
        typer.echo(
            f"    pdf_path={getattr(result, 'pdf_path') or ''} "
            f"page={getattr(result, 'pdf_page_start') or ''}"
        )
        typer.echo(f"    pdf_open_url={getattr(result, 'pdf_open_url') or ''}")
        typer.echo(f"    zotero_open_url={getattr(result, 'zotero_open_url') or ''}")
        related_note_titles = getattr(result, "related_note_titles")
        typer.echo(
            "    related_note_titles="
            + (", ".join(related_note_titles) if related_note_titles else "[]")
        )
        chunk_tags = getattr(result, "chunk_tags", [])
        typer.echo("    chunk_tags=" + (", ".join(chunk_tags) if chunk_tags else "[]"))


def echo_relation(relation: object) -> None:
    typer.echo(
        f"relation_id={getattr(relation, 'relation_id')}\t"
        f"source={getattr(relation, 'source_type')}:{getattr(relation, 'source_id')}\t"
        f"relation_type={getattr(relation, 'relation_type')}\t"
        f"target={getattr(relation, 'target_type')}:{getattr(relation, 'target_id')}\t"
        f"evidence_chunk_id={getattr(relation, 'evidence_chunk_id') or ''}\t"
        f"note_id={getattr(relation, 'note_id') or ''}\t"
        f"confidence={getattr(relation, 'confidence') if getattr(relation, 'confidence') is not None else ''}"
    )
    typer.echo(
        f"    evidence_document_title={getattr(relation, 'evidence_document_title') or ''}\t"
        f"heading_path={getattr(relation, 'evidence_heading_path') or ''}\t"
        f"pdf_path={getattr(relation, 'evidence_pdf_path') or ''}\t"
        f"page={getattr(relation, 'evidence_pdf_page_start') or ''}\t"
        f"pdf_open_url={getattr(relation, 'evidence_pdf_open_url') or ''}"
    )


def format_related_relation_ids(relations: list[object]) -> str:
    if not relations:
        return "[]"
    values = []
    for relation in relations:
        relation_id = getattr(relation, "relation_id", relation)
        values.append(str(relation_id))
    return ", ".join(values)
