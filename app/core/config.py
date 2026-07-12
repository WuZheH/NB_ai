from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from app.core.paths import DEFAULT_DB_PATH


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    sqlite_db_path: str = str(DEFAULT_DB_PATH)
    vector_store_worker_enabled: bool = _env_bool("NOTEBOOK_AI_VECTOR_STORE_WORKER_ENABLED", True)
    vector_store_auto_sync_enabled: bool = _env_bool("NOTEBOOK_AI_VECTOR_STORE_AUTO_SYNC_ENABLED", False)

    @property
    def database_url(self) -> str:
        return f"sqlite:///{Path(self.sqlite_db_path).as_posix()}"


settings = Settings()
