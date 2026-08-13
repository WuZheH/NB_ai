from __future__ import annotations

# Zotero runtime operations are currently exposed through FastAPI and scripts,
# not Typer commands.  This explicit empty surface prevents inventing a CLI
# contract while keeping the command-domain layout discoverable.
ZOTERO_COMMANDS: tuple[str, ...] = ()

__all__ = ["ZOTERO_COMMANDS"]
