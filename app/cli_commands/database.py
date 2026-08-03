from __future__ import annotations

import typer
from sqlalchemy import inspect

from app.cli_commands.shared import register_commands
from app.db.init_db import init_db
from app.db.session import engine


def init_db_command() -> None:
    """Create the SQLite database and Phase 1 core tables."""
    init_db()
    typer.echo("Database initialized.")


def show_tables_command() -> None:
    """List tables in the configured SQLite database."""
    table_names = inspect(engine).get_table_names()
    if not table_names:
        typer.echo("No tables found.")
        return

    for table_name in table_names:
        typer.echo(table_name)


def register_database_commands(app: typer.Typer) -> None:
    register_commands(
        app,
        namespace="database",
        commands=(
            ("init-db", init_db_command),
            ("show-tables", show_tables_command),
        ),
    )


__all__ = ["init_db_command", "register_database_commands", "show_tables_command"]
