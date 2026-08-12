from __future__ import annotations

"""Stable CLI import and module-execution façade."""

from app import cli_runtime as _runtime
from app.cli_runtime import *  # noqa: F401,F403


app = _runtime.app
inspiration_card_app = _runtime.inspiration_card_app


def __getattr__(name: str):
    return getattr(_runtime, name)


if __name__ == "__main__":
    app()
