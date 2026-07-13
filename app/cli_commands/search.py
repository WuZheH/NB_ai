from __future__ import annotations

import typer

from app.cli_commands.shared import echo_scored_results, register_commands
from app.services.hybrid_search_service import hybrid_search
from app.services.keyword_search_service import search_keywords
from app.services.vector_index_service import (
    VectorIndexModelMismatchError,
    VectorIndexNotFoundError,
    rebuild_vector_index,
    vector_search,
)


def search_command(
    query: str,
    document_type: str | None = typer.Option(None, "--document-type"),
    content_layer: str | None = typer.Option(None, "--content-layer"),
    research_direction: str | None = typer.Option(None, "--research-direction"),
    read_status: str | None = typer.Option(None, "--read-status"),
    limit: int = typer.Option(10, "--limit", min=1),
) -> None:
    """Search evidence chunks with SQLite LIKE."""
    results = search_keywords(
        query=query,
        document_type=document_type,
        content_layer=content_layer,
        research_direction=research_direction,
        read_status=read_status,
        limit=limit,
    )
    if not results:
        typer.echo(f'No search results found for "{query}".')
        return

    for index, result in enumerate(results, start=1):
        typer.echo(f"[{index}] {result.document_title}")
        typer.echo(
            f"    document_id={result.document_id} "
            f"type={result.document_type} layer={result.content_layer}"
        )
        typer.echo(f"    heading_path={result.heading_path}")
        typer.echo(f"    chunk_id={result.chunk_id}")
        typer.echo(f"    snippet={result.chunk_text_snippet}")
        typer.echo(f"    pdf_path={result.pdf_path or ''} page={result.pdf_page_start or ''}")
        typer.echo(f"    pdf_open_url={result.pdf_open_url or ''}")
        typer.echo(f"    zotero_open_url={result.zotero_open_url or ''}")
        typer.echo(
            "    related_note_titles="
            + (", ".join(result.related_note_titles) if result.related_note_titles else "[]")
        )
        typer.echo("    chunk_tags=" + (", ".join(result.chunk_tags) if result.chunk_tags else "[]"))


def rebuild_vector_index_command(
    embedder: str = typer.Option("hash-text-v1", "--embedder"),
) -> None:
    """Rebuild the local Phase 5A vector index from SQLite knowledge_chunks."""
    try:
        manifest = rebuild_vector_index(embedder_name=embedder)
    except (RuntimeError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(
        "Vector index rebuilt: "
        f"chunk_count={manifest.chunk_count}, "
        f"embedding_model={manifest.embedding_model}, "
        f"dimension={manifest.embedding_dimension}, "
        f"embedder_type={manifest.embedder_type}, "
        f"chunks_path={manifest.chunks_path}, "
        f"manifest_path={manifest.manifest_path}"
    )


def vector_search_command(
    query: str,
    document_type: str | None = typer.Option(None, "--document-type"),
    content_layer: str | None = typer.Option(None, "--content-layer"),
    read_status: str | None = typer.Option(None, "--read-status"),
    limit: int = typer.Option(10, "--limit", min=1),
    embedder: str | None = typer.Option(None, "--embedder"),
) -> None:
    """Search evidence chunks with the local Phase 5A vector index."""
    try:
        results = vector_search(
            query=query,
            limit=limit,
            document_type=document_type,
            content_layer=content_layer,
            read_status=read_status,
            embedder_name=embedder,
        )
    except (VectorIndexNotFoundError, VectorIndexModelMismatchError, RuntimeError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    if not results:
        typer.echo(f'No vector search results found for "{query}".')
        return
    echo_scored_results(results, score_label="score")


def hybrid_search_command(
    query: str,
    document_type: str | None = typer.Option(None, "--document-type"),
    content_layer: str | None = typer.Option(None, "--content-layer"),
    read_status: str | None = typer.Option(None, "--read-status"),
    limit: int = typer.Option(10, "--limit", min=1),
    embedder: str | None = typer.Option(None, "--embedder"),
) -> None:
    """Search evidence chunks with keyword + local vector score fusion."""
    try:
        results = hybrid_search(
            query=query,
            limit=limit,
            document_type=document_type,
            content_layer=content_layer,
            read_status=read_status,
            embedder_name=embedder,
        )
    except (VectorIndexNotFoundError, VectorIndexModelMismatchError, RuntimeError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    if not results:
        typer.echo(f'No hybrid search results found for "{query}".')
        return
    echo_scored_results(results, score_label="final_score")


def register_search_commands(app: typer.Typer) -> None:
    register_commands(
        app,
        namespace="search",
        commands=(
            ("search", search_command),
            ("rebuild-vector-index", rebuild_vector_index_command),
            ("vector-search", vector_search_command),
            ("hybrid-search", hybrid_search_command),
        ),
    )


__all__ = [
    "hybrid_search_command",
    "rebuild_vector_index_command",
    "register_search_commands",
    "search_command",
    "vector_search_command",
]
