from __future__ import annotations


class ConfigurationError(RuntimeError):
    """Raised when required application configuration is invalid."""


class DatabaseAccessError(RuntimeError):
    """Raised by callers that translate a database access failure."""


class ResourceNotFoundError(FileNotFoundError):
    """Raised when a requested local application resource is unavailable."""


class ValidationError(ValueError):
    """Raised when an application contract fails validation."""
