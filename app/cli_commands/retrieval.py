from __future__ import annotations

import typer

from app.cli_commands.research import research_copilot_command, research_session_command
from app.cli_commands.shared import format_related_relation_ids, register_commands
from app.services.retrieval_fusion_service import search_retrieval


def retrieval_search_command(
    query: str,
    top_k: int = typer.Option(10, "--top-k", min=1),
    rerank: str = typer.Option("heuristic", "--rerank"),
) -> None:
    """Search the read library with local keyword/vector/tag/relation/note-link fusion."""
    try:
        report = search_retrieval(query=query, top_k=top_k, rerank=rerank)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Retrieval Search Results: {report.query}")
    typer.echo(f"rerank={report.rerank}")
    typer.echo(f"external_rerank_called={report.external_rerank_called}")
    typer.echo(f"degraded_reason={report.degraded_reason or ''}")
    if report.local_degraded_reasons:
        typer.echo("local_degraded_reasons:")
        for reason in report.local_degraded_reasons:
            typer.echo(f"  - {reason}")
    if not report.results:
        typer.echo("  []")
        return
    for index, result in enumerate(report.results, start=1):
        typer.echo(
            f"[{index}] result_type={result.result_type}\tchunk_id={result.chunk_id}\t"
            f"document_id={result.document_id}\tdocument_title={result.document_title}\t"
            f"heading_path={result.heading_path}"
        )
        typer.echo(
            f"    fusion_score={result.fusion_score:.4f}\t"
            f"rerank_score={result.rerank_score:.4f}\t"
            f"sources={', '.join(result.source_channels) if result.source_channels else '[]'}"
        )
        typer.echo(
            f"    matched_terms={', '.join(result.matched_terms) if result.matched_terms else '[]'}\t"
            f"tag_match_count={result.tag_match_count}\t"
            f"related_note_count={result.related_note_count}\t"
            f"relation_count={result.relation_count}"
        )
        typer.echo(f"    snippet={result.snippet}")
        typer.echo(
            f"    pdf_path={result.pdf_path or ''}\tpage={result.pdf_page_start or ''}\t"
            f"pdf_open_url={result.pdf_open_url or ''}\tzotero_open_url={result.zotero_open_url or ''}"
        )
        typer.echo("    tags=" + (", ".join(result.tags) if result.tags else "[]"))
        typer.echo("    related_notes=" + (", ".join(result.related_notes) if result.related_notes else "[]"))
        typer.echo("    related_relations=" + format_related_relation_ids(result.related_relations))


def register_retrieval_commands(app: typer.Typer) -> None:
    register_commands(
        app,
        namespace="retrieval",
        commands=(("retrieval-search", retrieval_search_command),),
    )


__all__ = [
    "register_retrieval_commands",
    "research_copilot_command",
    "research_session_command",
    "retrieval_search_command",
]
