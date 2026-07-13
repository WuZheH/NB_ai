from __future__ import annotations

"""Typer composition root behind the stable :mod:`app.cli` façade."""

import typer

from app.cli_commands.database import (
    init_db_command,
    register_database_commands,
    show_tables_command,
)
from app.cli_commands.importing import (
    import_md_command,
    import_pdf_command,
    list_documents_command,
    register_importing_commands,
    show_chunks_command,
)
from app.cli_commands.inspirations import (
    inspiration_card_archive_command,
    inspiration_card_confirm_command,
    inspiration_card_create_command,
    inspiration_card_delete_command,
    inspiration_card_list_command,
    inspiration_card_plan_promotion_command,
    inspiration_card_reject_command,
    inspiration_card_show_command,
    inspiration_card_supersede_command,
    register_inspiration_card_commands,
)
from app.cli_commands.library import (
    library_home_command,
    library_search_command,
    library_show_chunk_command,
    library_show_document_command,
    library_show_note_command,
    list_read_books_command,
    register_library_commands,
    show_library_document_command,
    show_library_evidence_command,
    show_library_notes_command,
)
from app.cli_commands.notes import (
    import_note_command,
    link_note_command,
    list_chunk_notes_command,
    list_note_evidence_command,
    list_notes_command,
    register_note_commands,
    show_note_command,
)
from app.cli_commands.relations import (
    create_relation_command,
    list_relations_command,
    list_relations_for_chunk_command,
    list_relations_for_note_command,
    list_relations_for_tag_command,
    register_relation_commands,
    show_relation_command,
)
from app.cli_commands.research import (
    generate_hypothesis_command,
    register_research_commands,
    research_copilot_command,
    research_session_command,
)
from app.cli_commands.retrieval import register_retrieval_commands, retrieval_search_command
from app.cli_commands.search import (
    hybrid_search_command,
    rebuild_vector_index_command,
    register_search_commands,
    search_command,
    vector_search_command,
)
from app.cli_commands.tags import (
    create_tag_command,
    list_chunk_tags_command,
    list_note_tags_command,
    list_tagged_chunks_command,
    list_tagged_notes_command,
    list_tags_command,
    register_tag_commands,
    tag_chunk_command,
    tag_note_command,
)


app = typer.Typer(help="Research memory system CLI.")
inspiration_card_app = typer.Typer(help="InspirationCard manual lifecycle commands.")
app.add_typer(inspiration_card_app, name="inspiration-card")

register_database_commands(app)
register_importing_commands(app)
register_search_commands(app)
register_note_commands(app)
register_tag_commands(app)
register_relation_commands(app)
register_library_commands(app)
register_research_commands(app)
register_retrieval_commands(app)
register_inspiration_card_commands(inspiration_card_app)


if __name__ == "__main__":
    app()
