from __future__ import annotations

"""Domain-owned command implementations for the stable Typer application."""

from typing import Any

__all__ = ["app", "inspiration_card_app"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from app import cli_runtime

        return getattr(cli_runtime, name)
    raise AttributeError(name)
