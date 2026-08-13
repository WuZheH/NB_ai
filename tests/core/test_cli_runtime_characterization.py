from __future__ import annotations

import typer
from typer.testing import CliRunner

from app import cli, cli_runtime
from app.cli_commands import database as database_commands
from app.cli_commands import importing as importing_commands
from app.cli_commands import inspirations as inspiration_commands
from app.cli_commands import library as library_commands
from app.cli_commands import notes as note_commands
from app.cli_commands import relations as relation_commands
from app.cli_commands import research as research_commands
from app.cli_commands import retrieval as retrieval_commands
from app.cli_commands import search as search_commands
from app.cli_commands import tags as tag_commands


RUNNER = CliRunner()


def _command_paths() -> list[str]:
    root = typer.main.get_command(cli.app)
    paths: list[str] = []

    def walk(command, prefix: str = "") -> None:
        for name, child in (getattr(command, "commands", None) or {}).items():
            path = f"{prefix} {name}".strip()
            paths.append(path)
            walk(child, path)

    walk(root)
    return paths


def test_all_command_help_surfaces_remain_readable() -> None:
    for path in [""] + _command_paths():
        arguments = path.split() if path else []
        result = RUNNER.invoke(cli.app, [*arguments, "--help"], color=False)
        assert result.exit_code == 0, path
        assert "--help" in result.output

    assert "Research memory system CLI." in RUNNER.invoke(cli.app, ["--help"]).output
    assert "Search evidence chunks with SQLite LIKE." in RUNNER.invoke(
        cli.app, ["search", "--help"]
    ).output
    assert "InspirationCard manual lifecycle commands." in RUNNER.invoke(
        cli.app, ["inspiration-card", "--help"]
    ).output


def test_registration_functions_are_idempotent() -> None:
    root_command_count = len(cli.app.registered_commands)
    child_command_count = len(cli.inspiration_card_app.registered_commands)

    database_commands.register_database_commands(cli.app)
    importing_commands.register_importing_commands(cli.app)
    search_commands.register_search_commands(cli.app)
    note_commands.register_note_commands(cli.app)
    tag_commands.register_tag_commands(cli.app)
    relation_commands.register_relation_commands(cli.app)
    library_commands.register_library_commands(cli.app)
    research_commands.register_research_commands(cli.app)
    retrieval_commands.register_retrieval_commands(cli.app)
    inspiration_commands.register_inspiration_card_commands(cli.inspiration_card_app)

    assert len(cli.app.registered_commands) == root_command_count
    assert len(cli.inspiration_card_app.registered_commands) == child_command_count
    assert len(_command_paths()) == 53


def test_cli_runtime_reexports_domain_owned_command_callbacks() -> None:
    assert cli_runtime.init_db_command is database_commands.init_db_command
    assert cli_runtime.import_pdf_command is importing_commands.import_pdf_command
    assert cli_runtime.search_command is search_commands.search_command
    assert cli_runtime.import_note_command is note_commands.import_note_command
    assert cli_runtime.create_tag_command is tag_commands.create_tag_command
    assert cli_runtime.create_relation_command is relation_commands.create_relation_command
    assert cli_runtime.library_search_command is library_commands.library_search_command
    assert cli_runtime.research_session_command is research_commands.research_session_command
    assert cli_runtime.retrieval_search_command is retrieval_commands.retrieval_search_command
    assert (
        cli_runtime.inspiration_card_create_command
        is inspiration_commands.inspiration_card_create_command
    )


def test_safe_typical_command_stdout_contracts(monkeypatch) -> None:
    monkeypatch.setattr(database_commands, "init_db", lambda: None)
    initialized = RUNNER.invoke(cli.app, ["init-db"])
    assert initialized.exit_code == 0
    assert initialized.output == "Database initialized.\n"

    monkeypatch.setattr(search_commands, "search_keywords", lambda **_kwargs: [])
    searched = RUNNER.invoke(cli.app, ["search", "EDSR"])
    assert searched.exit_code == 0
    assert searched.output == 'No search results found for "EDSR".\n'

    monkeypatch.setattr(note_commands, "list_personal_notes", lambda **_kwargs: [])
    listed = RUNNER.invoke(cli.app, ["list-notes"])
    assert listed.exit_code == 0
    assert listed.output == "No personal notes found.\n"


def test_existing_error_exit_codes_remain_stable(monkeypatch) -> None:
    unknown = RUNNER.invoke(cli.app, ["does-not-exist"])
    assert unknown.exit_code == 2

    missing = RUNNER.invoke(cli.app, ["inspiration-card", "show"])
    assert missing.exit_code == 2

    invalid_limit = RUNNER.invoke(cli.app, ["search", "EDSR", "--limit", "0"])
    assert invalid_limit.exit_code == 2

    def fail_hybrid_search(**_kwargs):
        raise ValueError("vector unavailable")

    monkeypatch.setattr(search_commands, "hybrid_search", fail_hybrid_search)
    service_error = RUNNER.invoke(cli.app, ["hybrid-search", "EDSR"])
    assert service_error.exit_code == 1
    assert service_error.output == "vector unavailable\n"

    unsupported_write = RUNNER.invoke(
        cli.app,
        ["generate-hypothesis", "EDSR", "--no-dry-run"],
    )
    assert unsupported_write.exit_code == 2
    assert "Phase 8A only supports --dry-run." in unsupported_write.output
