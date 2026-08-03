from __future__ import annotations

"""Compatibility imports for the former maintenance command surface."""

from app.cli_commands.database import init_db_command, show_tables_command
from app.cli_commands.importing import list_documents_command, show_chunks_command

__all__ = [
    "init_db_command",
    "list_documents_command",
    "show_chunks_command",
    "show_tables_command",
]
