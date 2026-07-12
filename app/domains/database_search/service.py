"""Public database search orchestration entry point."""

from app.services.database_search_service import build_database_search

__all__ = ["build_database_search"]
