from __future__ import annotations

import logging


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a namespaced logger without configuring global logging."""
    return logging.getLogger(name or "app")
