from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.paths import DATA_DIR, DEFAULT_DB_PATH
from app.services import retrieval_generation_service


class ProductionWriteSurfaceFrozenError(RuntimeError):
    """Stable pre-write refusal for legacy production mutation surfaces."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        reason_code: str,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = 503
        self.reason_code = reason_code

    def detail(self) -> dict[str, Any]:
        return {
            "status": "error",
            "error_code": self.error_code,
            "message": str(self),
            "reason_code": self.reason_code,
            "retryable": False,
            "safe_to_retry": False,
            "writes_performed": False,
            "production_data_modified": False,
        }


def require_proven_legacy_for_legacy_write_surface(
    *,
    error_code: str,
    message: str,
    db_path: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> None:
    """Allow a legacy write surface only for a proven coherent legacy root.

    Explicit temporary databases remain available to tests and isolated tools.
    The formal production target must resolve as legacy; versioned, degraded,
    activating, corrupt, or otherwise ambiguous state is frozen before the
    caller performs any filesystem or database mutation.
    """

    database = Path(db_path if db_path is not None else DEFAULT_DB_PATH).resolve(
        strict=False
    )
    data_root = Path(data_dir if data_dir is not None else DATA_DIR).resolve(
        strict=False
    )
    production_database = Path(DEFAULT_DB_PATH).resolve(strict=False)
    production_data_root = Path(DATA_DIR).resolve(strict=False)
    database_is_production = database == production_database
    data_root_is_production = data_root == production_data_root
    if database_is_production and not data_root_is_production:
        raise ProductionWriteSurfaceFrozenError(
            error_code,
            message,
            reason_code="production_write_target_ambiguous",
        )
    if not database_is_production:
        return

    try:
        generation = retrieval_generation_service.current_retrieval_generation(
            data_dir=data_root,
            db_path=database,
        )
    except retrieval_generation_service.RetrievalGenerationError as exc:
        raise ProductionWriteSurfaceFrozenError(
            error_code,
            message,
            reason_code=exc.code,
        ) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProductionWriteSurfaceFrozenError(
            error_code,
            message,
            reason_code="retrieval_generation_state_unavailable",
        ) from exc

    if generation.mode != "legacy":
        raise ProductionWriteSurfaceFrozenError(
            error_code,
            message,
            reason_code="versioned_retrieval_generation_active",
        )
