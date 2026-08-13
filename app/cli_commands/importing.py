from __future__ import annotations

import typer

from app.cli_commands.notes import import_note_command, link_note_command
from app.cli_commands.shared import register_commands
from app.services.import_service import import_markdown_file, list_chunks, list_documents
from app.services.pdf_conversion_service import import_pdf


def import_md_command(path: str) -> None:
    """Import a Markdown file into documents, markdown_nodes, and knowledge_chunks."""
    result = import_markdown_file(path)
    typer.echo(
        "Imported markdown: "
        f"document_id={result.document_id}, "
        f"nodes_created={result.nodes_created}, "
        f"nodes_updated={result.nodes_updated}, "
        f"chunks_created={result.chunks_created}, "
        f"chunks_updated={result.chunks_updated}, "
        f"chunks_unchanged={result.chunks_unchanged}"
    )


def list_documents_command() -> None:
    """List imported documents."""
    documents = list_documents()
    if not documents:
        typer.echo("No documents found.")
        return

    for document in documents:
        typer.echo(
            f"{document.id}\t{document.title}\t"
            f"{document.document_type}\t{document.content_layer}\t{document.source_path}"
        )


def show_chunks_command(document_id: int = typer.Option(..., "--document-id")) -> None:
    """Show chunks for a document."""
    chunks = list_chunks(document_id)
    if not chunks:
        typer.echo("No chunks found.")
        return

    for chunk in chunks:
        snippet = chunk.chunk_text.replace("\n", " ")[:120]
        typer.echo(
            f"chunk_id={chunk.id}\tnode_id={chunk.node_id}\t"
            f"chunk_index={chunk.chunk_index}\tpage={chunk.pdf_page_start}\t"
            f"path={chunk.heading_path}\ttext={snippet}"
        )


def import_pdf_command(
    pdf_path: str,
    document_type: str = typer.Option(..., "--document-type"),
    read_status: str = typer.Option(..., "--read-status"),
    start_page: int | None = typer.Option(None, "--start-page"),
    end_page: int | None = typer.Option(None, "--end-page"),
) -> None:
    """Convert a read PDF to auto Markdown/layout JSON, then import it."""
    try:
        result = import_pdf(
            pdf_path=pdf_path,
            document_type=document_type,
            read_status=read_status,
            start_page=start_page,
            end_page=end_page,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if result.fallback_reason:
        typer.echo(f"PDF converter fallback: {result.fallback_reason}")
    typer.echo(
        "Imported PDF: "
        f"backend={result.backend}, "
        f"document_id={result.import_result.document_id}, "
        f"auto_md={result.converted_md_path}, "
        f"layout_json={result.layout_json_path}, "
        f"chunks_created={result.import_result.chunks_created}, "
        f"chunks_updated={result.import_result.chunks_updated}, "
        f"chunks_unchanged={result.import_result.chunks_unchanged}"
    )


def register_importing_commands(app: typer.Typer) -> None:
    register_commands(
        app,
        namespace="importing",
        commands=(
            ("import-md", import_md_command),
            ("list-documents", list_documents_command),
            ("show-chunks", show_chunks_command),
            ("import-pdf", import_pdf_command),
        ),
    )


__all__ = [
    "import_md_command",
    "import_note_command",
    "import_pdf_command",
    "link_note_command",
    "list_documents_command",
    "register_importing_commands",
    "show_chunks_command",
]
