from __future__ import annotations

"""Internal Typer command implementation for the stable ``app.cli`` façade."""

import json

import typer
from sqlalchemy import inspect

from app.db.init_db import init_db
from app.db.session import engine
from app.services.hybrid_search_service import hybrid_search
from app.services.hypothesis_service import generate_hypothesis_dry_run
from app.services.import_service import import_markdown_file, list_chunks, list_documents
from app.services import inspiration_card_service
from app.services.inspiration_card_service import CardSourceInput
from app.services.inspiration_card_promotion_planner import plan_inspiration_card_promotion
from app.services.keyword_search_service import search_keywords
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
from app.services.personal_note_service import (
    import_note_md,
    link_note_to_chunk,
    list_chunk_notes,
    list_note_evidence,
    list_personal_notes,
    show_personal_note,
)
from app.services.pdf_conversion_service import import_pdf
from app.services.relation_service import (
    create_relation,
    list_relations,
    list_relations_for_chunk,
    list_relations_for_note,
    list_relations_for_tag,
    show_relation,
)
from app.services.research_copilot_service import build_research_copilot_sections, run_research_copilot_dry_run
from app.services.research_session_service import build_research_session_sections, run_research_session_dry_run
from app.services.retrieval_fusion_service import search_retrieval
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
from app.services.vector_index_service import (
    VectorIndexNotFoundError,
    VectorIndexModelMismatchError,
    rebuild_vector_index,
    vector_search,
)

app = typer.Typer(help="Research memory system CLI.")
inspiration_card_app = typer.Typer(help="InspirationCard manual lifecycle commands.")
app.add_typer(inspiration_card_app, name="inspiration-card")


@app.command("init-db")
def init_db_command() -> None:
    """Create the SQLite database and Phase 1 core tables."""
    init_db()
    typer.echo("Database initialized.")


@app.command("show-tables")
def show_tables_command() -> None:
    """List tables in the configured SQLite database."""
    table_names = inspect(engine).get_table_names()
    if not table_names:
        typer.echo("No tables found.")
        return

    for table_name in table_names:
        typer.echo(table_name)


@app.command("import-md")
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


@app.command("list-documents")
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


@app.command("show-chunks")
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


@app.command("import-pdf")
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


@app.command("search")
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


@app.command("rebuild-vector-index")
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


@app.command("vector-search")
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
    _echo_scored_results(results, score_label="score")


@app.command("hybrid-search")
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
    _echo_scored_results(results, score_label="final_score")


@app.command("import-note")
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


@app.command("list-notes")
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


@app.command("show-note")
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


@app.command("link-note")
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


@app.command("list-note-evidence")
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


@app.command("list-chunk-notes")
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


@app.command("create-tag")
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


@app.command("list-tags")
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


@app.command("tag-chunk")
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


@app.command("tag-note")
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


@app.command("list-chunk-tags")
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


@app.command("list-note-tags")
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


@app.command("list-tagged-chunks")
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


@app.command("list-tagged-notes")
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


@app.command("create-relation")
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


@app.command("list-relations")
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
        _echo_relation(relation)


@app.command("show-relation")
def show_relation_command(relation_id: int = typer.Option(..., "--relation-id")) -> None:
    """Show one knowledge relation with evidence details."""
    try:
        relation = show_relation(relation_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _echo_relation(relation)


@app.command("list-relations-for-tag")
def list_relations_for_tag_command(tag_id: int = typer.Option(..., "--tag-id")) -> None:
    try:
        relations = list_relations_for_tag(tag_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not relations:
        typer.echo("No relations found.")
        return
    for relation in relations:
        _echo_relation(relation)


@app.command("list-relations-for-note")
def list_relations_for_note_command(note_id: int = typer.Option(..., "--note-id")) -> None:
    try:
        relations = list_relations_for_note(note_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not relations:
        typer.echo("No relations found.")
        return
    for relation in relations:
        _echo_relation(relation)


@app.command("list-relations-for-chunk")
def list_relations_for_chunk_command(chunk_id: int = typer.Option(..., "--chunk-id")) -> None:
    try:
        relations = list_relations_for_chunk(chunk_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not relations:
        typer.echo("No relations found.")
        return
    for relation in relations:
        _echo_relation(relation)


@app.command("library-home")
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


@app.command("library-search")
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


@app.command("library-show-document")
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


@app.command("library-show-note")
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


@app.command("library-show-chunk")
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


@app.command("list-read-books")
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


@app.command("show-library-document")
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


@app.command("show-library-notes")
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


@app.command("show-library-evidence")
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


@app.command("generate-hypothesis")
def generate_hypothesis_command(
    question: str,
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    limit: int = typer.Option(5, "--limit", min=1),
) -> None:
    """Phase 8A dry-run evidence preparation for a research question."""
    if not dry_run:
        raise typer.BadParameter("Phase 8A only supports --dry-run. LLM/API generation is not implemented.")
    try:
        report = generate_hypothesis_dry_run(question=question, limit=limit)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo("Phase 8A dry-run evidence report")
    typer.echo(f"research_question={report.research_question}")
    typer.echo("note=This command does not generate final innovation points or final research hypotheses.")

    typer.echo("evidence_chunks:")
    if not report.evidence_chunks:
        typer.echo("  []")
    for item in report.evidence_chunks:
        typer.echo(
            f"  chunk_id={item.chunk_id}\tdocument_title={item.document_title}\t"
            f"heading_path={item.heading_path}\tpdf_path={item.pdf_path or ''}\t"
            f"page={item.pdf_page_start or ''}\tchunk_tags={', '.join(item.chunk_tags) if item.chunk_tags else '[]'}"
        )
        typer.echo(f"    snippet={item.chunk_text_snippet}")

    typer.echo("related_notes:")
    if not report.related_notes:
        typer.echo("  []")
    for note in report.related_notes:
        typer.echo(
            f"  note_id={note.note_id}\ttitle={note.title}\tnote_type={note.note_type}\t"
            f"linked_chunk_ids={note.linked_chunk_ids}\tnote_tags={', '.join(note.note_tags) if note.note_tags else '[]'}"
        )
        typer.echo(f"    snippet={note.snippet}")

    typer.echo("related_tags:")
    if not report.related_tags:
        typer.echo("  []")
    for tag in report.related_tags:
        typer.echo(
            f"  tag_id={tag.tag_id}\tname={tag.name}\ttag_type={tag.tag_type}\t"
            f"description={tag.description or ''}"
        )

    typer.echo("related_relations:")
    if not report.related_relations:
        typer.echo("  []")
    for relation in report.related_relations:
        _echo_relation(relation)

    typer.echo("evidence_gaps:")
    if not report.evidence_gaps:
        typer.echo("  []")
    for gap in report.evidence_gaps:
        typer.echo(f"  - {gap}")

    typer.echo("suggested_next_actions:")
    for step in report.suggested_next_actions:
        typer.echo(f"  - {step}")

    typer.echo("execution_flags:")
    typer.echo(f"  dry_run={report.dry_run}")
    typer.echo(f"  llm_called={report.llm_called}")
    typer.echo(f"  api_called={report.api_called}")
    typer.echo(f"  final_hypothesis_generated={report.final_hypothesis_generated}")


@app.command("research-session")
def research_session_command(
    question: str,
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    top_k: int = typer.Option(5, "--top-k", min=1),
    rerank: str = typer.Option("heuristic", "--rerank"),
    output_format: str = typer.Option("text", "--format"),
    verify: bool = typer.Option(False, "--verify/--no-verify"),
) -> None:
    """Phase 9B local Research Session dry-run based on the internal read library."""
    if not dry_run:
        raise typer.BadParameter("Phase 9B.1 only supports --dry-run. Research generation is not implemented.")
    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be text or json.")
    try:
        report = run_research_session_dry_run(question, top_k=top_k, dry_run=dry_run, rerank=rerank)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    sections = build_research_session_sections(report, evidence_limit=top_k)
    if output_format == "json":
        typer.echo(json.dumps(sections, ensure_ascii=False, indent=2))
        return
    _echo_research_session_text(sections)


@app.command("research-copilot")
def research_copilot_command(
    question: str,
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    top_k: int = typer.Option(5, "--top-k", min=1),
    rerank: str = typer.Option("heuristic", "--rerank"),
    output_format: str = typer.Option("text", "--format"),
    verify: bool = typer.Option(False, "--verify/--no-verify"),
    multi_candidate: bool = typer.Option(False, "--multi-candidate/--single-candidate"),
) -> None:
    """Phase 10B local Research Copilot dry-run with controlled candidate drafts."""
    if not dry_run:
        raise typer.BadParameter("Phase 10B.0 only supports --dry-run. Controlled Research Copilot generation is not enabled.")
    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be text or json.")
    try:
        report = run_research_copilot_dry_run(
            question,
            top_k=top_k,
            dry_run=dry_run,
            rerank=rerank,
            verify=verify,
            multi_candidate=multi_candidate,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    sections = build_research_copilot_sections(report)
    if output_format == "json":
        typer.echo(json.dumps(sections, ensure_ascii=False, indent=2))
        return
    _echo_research_copilot_text(sections)


def _echo_research_copilot_text(sections: dict[str, object]) -> None:
    question = sections["question"]
    readiness = sections["evidence_readiness"]
    safety_flags = sections["safety_flags"]

    typer.echo("Research Copilot dry-run report")
    typer.echo("question:")
    typer.echo(f"  research_question={question['research_question']}")
    typer.echo(f"  top_k={question['top_k']}")
    typer.echo(f"status={sections['status']}")
    typer.echo(f"verify={sections['verify']}")
    typer.echo(f"multi_candidate={sections['multi_candidate']}")

    typer.echo("evidence_readiness:")
    typer.echo(f"  ready_for_hypothesis_dry_run={readiness['ready_for_hypothesis_dry_run']}")
    typer.echo("  blocking_gaps=" + ("[]" if not readiness["blocking_gaps"] else ""))
    for gap in readiness["blocking_gaps"]:
        typer.echo(f"    - {gap}")
    typer.echo("  warning_gaps=" + ("[]" if not readiness["warning_gaps"] else ""))
    for gap in readiness["warning_gaps"]:
        typer.echo(f"    - {gap}")

    typer.echo("candidate_hypothesis_drafts:")
    drafts = sections["candidate_hypothesis_drafts"]
    if not drafts:
        typer.echo("  []")
    for draft in drafts:
        typer.echo(f"  hypothesis_id={draft['hypothesis_id']}")
        typer.echo(f"    core_idea={draft['core_idea']}")
        typer.echo(f"    target_problem={draft['target_problem']}")
        typer.echo(f"    supporting_evidence_ids={draft['supporting_evidence_ids']}")
        typer.echo(f"    supporting_note_ids={draft['supporting_note_ids']}")
        typer.echo(f"    supporting_relation_ids={draft['supporting_relation_ids']}")
        typer.echo(f"    expected_difference_from_existing_methods={draft['expected_difference_from_existing_methods']}")
        typer.echo(f"    minimum_validation_experiment={draft['minimum_validation_experiment']}")
        typer.echo(f"    confidence_level={draft['confidence_level']}")
        typer.echo("    risks:")
        for risk in draft["risks"]:
            typer.echo(f"      - {risk}")
        typer.echo("    missing_evidence:")
        for item in draft["missing_evidence"]:
            typer.echo(f"      - {item}")

    if sections["verify"]:
        typer.echo("candidate_verifications:")
        verifications = sections["candidate_verifications"]
        if not verifications:
            typer.echo("  []")
        for verification in verifications:
            typer.echo(f"  hypothesis_id={verification['hypothesis_id']}")
            typer.echo(f"    verification_status={verification['verification_status']}")
            typer.echo(f"    evidence_support_score={verification['evidence_support_score']}")
            typer.echo(f"    minimum_validation_experiment_check={verification['minimum_validation_experiment_check']}")
            typer.echo(f"    downgrade_to_next_action={verification['downgrade_to_next_action']}")
            typer.echo(f"    unsupported_claims={verification['unsupported_claims']}")
            typer.echo(f"    missing_evidence={verification['missing_evidence']}")
            typer.echo(f"    risk_flags={verification['risk_flags']}")

        typer.echo("verified_candidate_hypothesis_drafts:")
        verified = sections["verified_candidate_hypothesis_drafts"]
        if not verified:
            typer.echo("  []")
        for draft in verified:
            typer.echo(f"  - {draft['hypothesis_id']}")

        typer.echo("downgraded_candidates:")
        downgraded = sections["downgraded_candidates"]
        if not downgraded:
            typer.echo("  []")
        for item in downgraded:
            typer.echo(f"  - hypothesis_id={item['hypothesis_id']}")
            typer.echo(f"    verification_status={item['verification_status']}")
            typer.echo(f"    suggested_next_action={item['suggested_next_action']}")

        typer.echo("critic_summary:")
        for key, value in sections["critic_summary"].items():
            typer.echo(f"  {key}={value}")

    typer.echo("human_review_queue:")
    review_queue = sections["human_review_queue"]
    if not review_queue:
        typer.echo("  []")
    for item in review_queue:
        typer.echo(f"  review_id={item['review_id']}")
        typer.echo(f"    hypothesis_id={item['hypothesis_id']}")
        typer.echo(f"    review_status={item['review_status']}")
        typer.echo(f"    recommended_action={item['recommended_action']}")
        typer.echo(f"    review_reason={item['review_reason']}")
        typer.echo(f"    evidence_to_inspect={item['evidence_to_inspect']}")
        typer.echo("    required_human_checks:")
        for check in item["required_human_checks"]:
            typer.echo(f"      - {check}")

    typer.echo(f"final_hypothesis={sections['final_hypothesis']}")
    typer.echo("external_candidate_queries:")
    queries = sections["external_candidate_queries"]
    if not queries:
        typer.echo("  []")
    for query in queries:
        typer.echo(f"  - {query}")

    typer.echo("suggested_next_actions:")
    for step in sections["suggested_next_actions"]:
        typer.echo(f"  - {step}")

    typer.echo("safety_flags:")
    for key in [
        "dry_run",
        "llm_called",
        "api_called",
        "external_search_called",
        "external_llm_called",
        "final_hypothesis_generated",
    ]:
        typer.echo(f"  {key}={safety_flags[key]}")


def _echo_research_session_text(sections: dict[str, object]) -> None:
    question = sections["question"]
    retrieval_summary = sections["retrieval_summary"]
    readiness = sections["readiness_judgement"]
    external_candidate_section = sections["external_candidate_section"]
    safety_flags = sections["safety_flags"]

    typer.echo("Research Session dry-run report")
    typer.echo("question:")
    typer.echo(f"  research_question={question['research_question']}")
    typer.echo(f"  top_k={question['top_k']}")

    typer.echo("retrieval_summary:")
    typer.echo(f"  total_results={retrieval_summary['total_results']}")
    typer.echo(f"  high_confidence_count={retrieval_summary['high_confidence_count']}")
    typer.echo(f"  evidence_backed_count={retrieval_summary['evidence_backed_count']}")
    typer.echo(f"  tag_or_relation_supported_count={retrieval_summary['tag_or_relation_supported_count']}")
    typer.echo(f"  vector_index_available={retrieval_summary['vector_index_available']}")
    typer.echo(f"  degraded_reason={retrieval_summary['degraded_reason'] or ''}")

    typer.echo("evidence_summary:")
    evidence_summary = sections["evidence_summary"]
    if not evidence_summary:
        typer.echo("  []")
    for index, evidence in enumerate(evidence_summary, start=1):
        typer.echo(
            f"  [{index}] title={evidence['document_title']}\t"
            f"heading_path={evidence['heading_path']}\tpage={evidence['pdf_page_start'] or ''}"
        )
        typer.echo(
            f"      source_channels={', '.join(evidence['source_channels']) if evidence['source_channels'] else '[]'}\t"
            f"confidence={evidence['confidence']}\t"
            f"fusion_score={evidence['fusion_score']:.4f}\trerank_score={evidence['rerank_score']:.4f}"
        )
        typer.echo(
            f"      matched_terms={', '.join(evidence['matched_terms']) if evidence['matched_terms'] else '[]'}\t"
            f"tag_match_count={evidence['tag_match_count']}\t"
            f"related_note_count={evidence['related_note_count']}\t"
            f"relation_count={evidence['relation_count']}"
        )
        typer.echo(f"      snippet={evidence['snippet']}")

    typer.echo("related_notes:")
    related_notes = sections["related_notes"]
    if not related_notes:
        typer.echo("  []")
    for note in related_notes[:5]:
        typer.echo(f"  note_id={note['note_id']}\ttitle={note['title']}\tnote_type={note['note_type']}")

    typer.echo("related_tags:")
    related_tags = sections["related_tags"]
    if not related_tags:
        typer.echo("  []")
    for tag in related_tags[:8]:
        typer.echo(f"  tag_id={tag['tag_id']}\tname={tag['name']}\ttag_type={tag['tag_type']}")

    typer.echo("related_relations:")
    related_relations = sections["related_relations"]
    if not related_relations:
        typer.echo("  []")
    for relation in related_relations[:5]:
        typer.echo(
            f"  relation_id={relation['relation_id']}\ttype={relation['relation_type']}\t"
            f"evidence_chunk_id={relation['evidence_chunk_id'] or ''}"
        )

    typer.echo("evidence_gaps:")
    evidence_gaps = sections["evidence_gaps"]
    if not evidence_gaps:
        typer.echo("  []")
    for gap in evidence_gaps:
        typer.echo(f"  - {gap}")

    typer.echo("readiness_judgement:")
    typer.echo(f"  ready_for_hypothesis_dry_run={readiness['ready_for_hypothesis_dry_run']}")
    typer.echo("  blocking_gaps=" + ("[]" if not readiness["blocking_gaps"] else ""))
    for gap in readiness["blocking_gaps"]:
        typer.echo(f"    - {gap}")
    typer.echo("  warning_gaps=" + ("[]" if not readiness["warning_gaps"] else ""))
    for gap in readiness["warning_gaps"]:
        typer.echo(f"    - {gap}")

    typer.echo("external_candidate_section:")
    typer.echo(f"  enabled={external_candidate_section['enabled']}")
    typer.echo(f"  called={external_candidate_section['called']}")
    typer.echo(f"  degraded_reason={external_candidate_section['degraded_reason']}")
    typer.echo(f"  safety_note={external_candidate_section['safety_note']}")
    typer.echo("  candidate_queries:")
    if not external_candidate_section["candidate_queries"]:
        typer.echo("    []")
    for query, reason in zip(
        external_candidate_section["candidate_queries"],
        external_candidate_section["reasons"],
    ):
        typer.echo(f"    - query={query}")
        typer.echo(f"      reason={reason}")

    typer.echo("suggested_next_actions:")
    for step in sections["suggested_next_actions"]:
        typer.echo(f"  - {step}")

    typer.echo("safety_flags:")
    for key in [
        "dry_run",
        "llm_called",
        "api_called",
        "external_api_enabled",
        "external_search_called",
        "external_rerank_called",
        "external_llm_called",
        "final_hypothesis_generated",
        "privacy_mode",
    ]:
        typer.echo(f"  {key}={safety_flags[key]}")
    typer.echo("external_call_audit:")
    audit_records = safety_flags.get("external_call_audit") or []
    if not audit_records:
        typer.echo("  []")
    for audit in audit_records:
        typer.echo(
            f"  feature={audit.get('feature')}\taction={audit.get('action')}\t"
            f"provider={audit.get('provider') or ''}\tallowed={audit.get('allowed')}\t"
            f"called={audit.get('called')}\tdegraded_reason={audit.get('degraded_reason') or ''}"
        )


@app.command("retrieval-search")
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
        typer.echo("    related_relations=" + _format_related_relation_ids(result.related_relations))


@inspiration_card_app.command("create")
def inspiration_card_create_command(
    title: str = typer.Option(..., "--title"),
    content: str = typer.Option(..., "--content"),
    created_by: str = typer.Option(..., "--created-by"),
    actor: str = typer.Option(..., "--actor"),
    source_doc_id: int | None = typer.Option(None, "--source-doc-id"),
    source_chunk_id: int | None = typer.Option(None, "--source-chunk-id"),
    source_gap_reason: str | None = typer.Option(None, "--source-gap-reason"),
    tag_id: list[int] | None = typer.Option(None, "--tag-id"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Create a candidate InspirationCard through the service layer."""
    sources = []
    if source_doc_id is not None or source_chunk_id is not None:
        sources.append(CardSourceInput(source_doc_id=source_doc_id, source_chunk_id=source_chunk_id))
    try:
        card = inspiration_card_service.create_card(
            title=title,
            content=content,
            created_by=created_by,
            actor=actor,
            sources=sources,
            tag_ids=list(tag_id or []),
            source_gap_reason=source_gap_reason,
            reason=reason,
        )
    except ValueError as exc:
        _raise_inspiration_card_error(exc)
    _echo_inspiration_card_write_result(card)


@inspiration_card_app.command("show")
def inspiration_card_show_command(card_id: int = typer.Option(..., "--card-id")) -> None:
    """Show one InspirationCard with sources, tags, and events."""
    try:
        card = inspiration_card_service.get_card(card_id)
    except ValueError as exc:
        _raise_inspiration_card_error(exc)
    _echo_inspiration_card_detail(card)


@inspiration_card_app.command("list")
def inspiration_card_list_command(
    status: str = typer.Option("candidate", "--status"),
    limit: int = typer.Option(50, "--limit", min=1),
) -> None:
    """List InspirationCards by DB lifecycle status."""
    try:
        cards = inspiration_card_service.list_cards_by_status(status=status, limit=limit)
    except ValueError as exc:
        _raise_inspiration_card_error(exc)
    if not cards:
        typer.echo("[]")
        return
    for card in cards:
        gap_marker = f"\tsource_gap_reason={card.source_gap_reason}" if card.source_gap_reason else ""
        typer.echo(
            f"card_id={card.card_id}\ttitle={card.title}\tstatus={card.status}\t"
            f"created_by={card.created_by}\tupdated_at={card.updated_at.isoformat()}{gap_marker}"
        )


@inspiration_card_app.command("confirm")
def inspiration_card_confirm_command(
    card_id: int = typer.Option(..., "--card-id"),
    actor: str = typer.Option(..., "--actor"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Transition candidate -> user-confirmed."""
    _transition_inspiration_card(card_id=card_id, new_status="user-confirmed", actor=actor, reason=reason)


@inspiration_card_app.command("reject")
def inspiration_card_reject_command(
    card_id: int = typer.Option(..., "--card-id"),
    actor: str = typer.Option(..., "--actor"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Transition candidate -> rejected."""
    _transition_inspiration_card(card_id=card_id, new_status="rejected", actor=actor, reason=reason)


@inspiration_card_app.command("archive")
def inspiration_card_archive_command(
    card_id: int = typer.Option(..., "--card-id"),
    actor: str = typer.Option(..., "--actor"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Transition a valid current status to archived."""
    _transition_inspiration_card(card_id=card_id, new_status="archived", actor=actor, reason=reason)


@inspiration_card_app.command("supersede")
def inspiration_card_supersede_command(
    card_id: int = typer.Option(..., "--card-id"),
    actor: str = typer.Option(..., "--actor"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Transition user-confirmed -> superseded."""
    _transition_inspiration_card(card_id=card_id, new_status="superseded", actor=actor, reason=reason)


@inspiration_card_app.command("delete")
def inspiration_card_delete_command(
    card_id: int = typer.Option(..., "--card-id"),
    actor: str = typer.Option(..., "--actor"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Transition archived or superseded cards to deleted."""
    _transition_inspiration_card(card_id=card_id, new_status="deleted", actor=actor, reason=reason)


@inspiration_card_app.command("plan-promotion")
def inspiration_card_plan_promotion_command(
    card_id: int = typer.Option(..., "--card-id"),
    target_type: str = typer.Option(..., "--target-type"),
    actor: str = typer.Option(..., "--actor"),
    promotion_reason: str = typer.Option(..., "--promotion-reason"),
    target_title: str | None = typer.Option(None, "--target-title"),
    target_description: str | None = typer.Option(None, "--target-description"),
    target_metadata_json: str | None = typer.Option(None, "--target-metadata-json"),
) -> None:
    """Plan a dry-run InspirationCard promotion without writing target objects."""
    target_metadata = _parse_target_metadata_json(target_metadata_json)
    if target_title is not None:
        target_metadata["target_title"] = target_title
    if target_description is not None:
        target_metadata["target_description"] = target_description

    try:
        card = inspiration_card_service.get_card(card_id)
    except ValueError:
        card = None

    plan = plan_inspiration_card_promotion(
        card=_promotion_card_input(card),
        target_type=target_type,
        actor=actor,
        promotion_reason=promotion_reason,
        target_metadata=target_metadata,
    )
    typer.echo(json.dumps(plan, ensure_ascii=False, sort_keys=True, default=str))
    if not plan.get("ok"):
        raise typer.Exit(code=1)


def _echo_scored_results(results: list[object], score_label: str) -> None:
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


def _transition_inspiration_card(card_id: int, new_status: str, actor: str, reason: str | None) -> None:
    try:
        card = inspiration_card_service.transition_card_status(
            card_id=card_id,
            new_status=new_status,
            actor=actor,
            reason=reason,
        )
    except ValueError as exc:
        _raise_inspiration_card_error(exc)
    _echo_inspiration_card_write_result(card)


def _raise_inspiration_card_error(exc: ValueError) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(code=1) from exc


def _parse_target_metadata_json(value: str | None) -> dict[str, object]:
    if value is None:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"target_metadata_json must be a JSON object: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("target_metadata_json must be a JSON object.")
    return parsed


def _promotion_card_input(card: object | None) -> dict[str, object] | None:
    if card is None:
        return None
    card_input = {
        "id": card.card_id,
        "card_id": card.card_id,
        "title": card.title,
        "content": card.content,
        "status": card.status,
        "created_by": card.created_by,
        "source_gap_reason": card.source_gap_reason,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
        "sources": [
            {
                "source_id": source.source_id,
                "source_doc_id": source.source_doc_id,
                "source_chunk_id": source.source_chunk_id,
                "created_at": source.created_at,
            }
            for source in card.sources
        ],
        "tags": [
            {
                "tag_id": tag.tag_id,
                "tag_name": tag.tag_name,
                "tag_type": tag.tag_type,
                "created_at": tag.created_at,
            }
            for tag in card.tags
        ],
        "events": [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "actor": event.actor,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "reason": event.reason,
                "created_at": event.created_at,
            }
            for event in card.events
        ],
    }
    if hasattr(card, "source_trace"):
        card_input["source_trace"] = getattr(card, "source_trace")
    return card_input


def _echo_inspiration_card_write_result(card: object) -> None:
    event = card.events[-1] if card.events else None
    typer.echo(f"card_id={card.card_id}")
    typer.echo(f"status={card.status}")
    if event is not None:
        typer.echo(
            f"event_id={event.event_id}\tevent_type={event.event_type}\tactor={event.actor}\t"
            f"from_status={event.from_status or ''}\tto_status={event.to_status or ''}"
        )


def _echo_inspiration_card_detail(card: object) -> None:
    typer.echo(f"card_id={card.card_id}")
    typer.echo(f"title={card.title}")
    typer.echo(f"content={card.content}")
    typer.echo(f"status={card.status}")
    typer.echo(f"created_by={card.created_by}")
    typer.echo(f"source_gap_reason={card.source_gap_reason or ''}")
    typer.echo(f"created_at={card.created_at.isoformat()}")
    typer.echo(f"updated_at={card.updated_at.isoformat()}")
    typer.echo("sources:")
    if not card.sources:
        typer.echo("  []")
    for source in card.sources:
        typer.echo(
            f"  source_id={source.source_id}\tsource_doc_id={source.source_doc_id or ''}\t"
            f"source_chunk_id={source.source_chunk_id or ''}\tcreated_at={source.created_at.isoformat()}"
        )
    typer.echo("tags:")
    if not card.tags:
        typer.echo("  []")
    for tag in card.tags:
        typer.echo(
            f"  binding_id={tag.binding_id}\ttag_id={tag.tag_id}\tname={tag.tag_name}\t"
            f"tag_type={tag.tag_type}\tcreated_at={tag.created_at.isoformat()}"
        )
    typer.echo("events:")
    if not card.events:
        typer.echo("  []")
    for event in card.events:
        typer.echo(
            f"  event_id={event.event_id}\tevent_type={event.event_type}\tactor={event.actor}\t"
            f"from_status={event.from_status or ''}\tto_status={event.to_status or ''}\t"
            f"reason={event.reason or ''}\tcreated_at={event.created_at.isoformat()}"
        )


def _echo_relation(relation: object) -> None:
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


def _format_related_relation_ids(relations: list[object]) -> str:
    if not relations:
        return "[]"
    values = []
    for relation in relations:
        relation_id = getattr(relation, "relation_id", relation)
        values.append(str(relation_id))
    return ", ".join(values)


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


if __name__ == "__main__":
    app()
