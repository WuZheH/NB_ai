from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Callable
from uuid import uuid4

from app.core.paths import DATA_DIR, DEFAULT_DB_PATH
from app.services import retrieval_generation_service as generations


class MutationStage(str, Enum):
    PREPARE = "prepare"
    PREPARED = "prepared"
    BODY_DB_MUTATED = "body_db_mutated"
    POST_WRITE_SNAPSHOT = "post_write_snapshot"
    CANDIDATE_SYNCED = "candidate_synced"
    CANDIDATE_VALIDATED = "candidate_validated"
    FINALIZED = "finalized"
    ACTIVATING = "activating"
    POINTER_SWITCHED = "pointer_switched"
    POST_SWITCH_VERIFIED = "post_switch_verified"
    ACTIVATION_CLEARED = "activation_cleared"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    DEGRADED = "degraded"


class ProductionGenerationMutationError(RuntimeError):
    pass


class ProductionGenerationProtocolError(ProductionGenerationMutationError):
    pass


class ProductionGenerationRollbackError(ProductionGenerationMutationError):
    def __init__(
        self,
        message: str,
        *,
        stage: MutationStage,
        rollback_substage: str,
        cause: BaseException,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.rollback_substage = rollback_substage
        self.cause = cause


@dataclass(frozen=True)
class DatabaseRollbackSnapshot:
    path: Path
    sha256: str
    size: int


RollbackSnapshotFactory = Callable[[Path], DatabaseRollbackSnapshot]
RollbackSnapshotRestorer = Callable[[Path, DatabaseRollbackSnapshot], None]
PointerRestorer = Callable[..., None]
CandidateValidator = Callable[[generations.CandidateGeneration, str], None]
ActiveValidator = Callable[[generations.RetrievalGenerationSnapshot], None]
OwnedTreeRemover = Callable[[Path], None]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sqlite_sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(
        path.with_name(path.name + suffix)
        for suffix in ("-journal", "-wal", "-shm")
    )


def create_database_rollback_snapshot(path: Path) -> DatabaseRollbackSnapshot:
    target = Path(path).resolve(strict=True)
    live_sidecars = [sidecar.name for sidecar in _sqlite_sidecars(target) if sidecar.exists()]
    if live_sidecars:
        raise ProductionGenerationMutationError(
            "production database rollback snapshot requires no live SQLite sidecars"
        )
    before_sha256 = _sha256_file(target)
    before_size = target.stat().st_size
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.generation-rollback-",
        suffix=".sqlite",
        dir=str(target.parent),
    )
    os.close(descriptor)
    rollback_path = Path(raw_path)
    try:
        shutil.copy2(target, rollback_path)
        if (
            rollback_path.stat().st_size != before_size
            or _sha256_file(rollback_path) != before_sha256
        ):
            raise ProductionGenerationMutationError(
                "production database rollback snapshot verification failed"
            )
    except BaseException:
        rollback_path.unlink(missing_ok=True)
        raise
    return DatabaseRollbackSnapshot(
        path=rollback_path,
        sha256=before_sha256,
        size=before_size,
    )


def restore_database_rollback_snapshot(
    path: Path,
    snapshot: DatabaseRollbackSnapshot,
) -> None:
    target = Path(path).resolve(strict=False)
    rollback = Path(snapshot.path).resolve(strict=True)
    if (
        rollback.stat().st_size != snapshot.size
        or _sha256_file(rollback) != snapshot.sha256
    ):
        raise ProductionGenerationMutationError(
            "production database rollback snapshot is no longer valid"
        )
    for sidecar in _sqlite_sidecars(target):
        sidecar.unlink(missing_ok=True)
    restore_candidate = target.with_name(
        f".{target.name}.{uuid4().hex}.generation-restore"
    )
    try:
        shutil.copy2(rollback, restore_candidate)
        if (
            restore_candidate.stat().st_size != snapshot.size
            or _sha256_file(restore_candidate) != snapshot.sha256
        ):
            raise ProductionGenerationMutationError(
                "production database restore candidate verification failed"
            )
        try:
            os.replace(restore_candidate, target)
        except PermissionError:
            with restore_candidate.open("rb") as source:
                with target.open("wb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                    destination.flush()
                    os.fsync(destination.fileno())
        if target.stat().st_size != snapshot.size or _sha256_file(target) != snapshot.sha256:
            raise ProductionGenerationMutationError(
                "production database rollback target verification failed"
            )
    finally:
        restore_candidate.unlink(missing_ok=True)


class ProductionGenerationMutationSession:
    """Own one production DB + immutable-generation activation transaction.

    Operation-specific code mutates the database and candidate artifacts, while
    this session owns the cross-cutting writer barrier, durable pointer protocol,
    verified database rollback, and fail-closed rollback ordering.
    """

    def __init__(
        self,
        *,
        data_dir: str | Path = DATA_DIR,
        db_path: str | Path = DEFAULT_DB_PATH,
        generation_id: str | None = None,
        rollback_snapshot_factory: RollbackSnapshotFactory = create_database_rollback_snapshot,
        rollback_snapshot_restorer: RollbackSnapshotRestorer = restore_database_rollback_snapshot,
        pointer_restorer: PointerRestorer = generations.restore_active_pointer,
        owned_tree_remover: OwnedTreeRemover | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).resolve(strict=False)
        self.db_path = Path(db_path).resolve(strict=False)
        self.generation_id = generation_id
        self._rollback_snapshot_factory = rollback_snapshot_factory
        self._rollback_snapshot_restorer = rollback_snapshot_restorer
        self._pointer_restorer = pointer_restorer
        self._owned_tree_remover = owned_tree_remover or shutil.rmtree

        self.previous_generation: generations.RetrievalGenerationSnapshot | None = None
        self.previous_pointer_bytes: bytes | None = None
        self.candidate: generations.CandidateGeneration | None = None
        self.rollback_snapshot: DatabaseRollbackSnapshot | None = None
        self.post_write_db_sha256: str | None = None
        self.finalized_generation: generations.RetrievalGenerationSnapshot | None = None

        self._writer_context = None
        self._entered = False
        self._pointer_switched = False
        self._activation_state_written = False
        self._stage: MutationStage | None = None
        self._stage_history: list[MutationStage] = []

    @property
    def stage(self) -> MutationStage:
        if self._stage is None:
            raise ProductionGenerationProtocolError("mutation session has not started")
        return self._stage

    @property
    def stage_history(self) -> tuple[MutationStage, ...]:
        return tuple(self._stage_history)

    def _transition(self, stage: MutationStage) -> None:
        self._stage = stage
        self._stage_history.append(stage)

    def _require_stage(self, expected: MutationStage) -> None:
        if self._stage is not expected:
            actual = self._stage.value if self._stage is not None else "not_started"
            raise ProductionGenerationProtocolError(
                f"mutation stage {actual!r} cannot perform operation requiring {expected.value!r}"
            )

    def __enter__(self) -> "ProductionGenerationMutationSession":
        if self._entered or self._writer_context is not None:
            raise ProductionGenerationProtocolError("mutation session cannot be entered twice")
        self._writer_context = generations.production_write_generation(
            data_dir=self.data_dir
        )
        self._writer_context.__enter__()
        self._entered = True
        self._transition(MutationStage.PREPARE)
        try:
            self.previous_generation = generations.resolve_active_retrieval_generation(
                data_dir=self.data_dir,
                db_path=self.db_path,
                verify_fingerprints=True,
            )
            generations.verify_generation_database_revision(
                self.previous_generation,
                self.db_path,
            )
            self.previous_pointer_bytes = generations.read_active_pointer_bytes(
                data_dir=self.data_dir
            )
            self.candidate = generations.prepare_candidate_generation(
                self.previous_generation,
                data_dir=self.data_dir,
                generation_id=self.generation_id,
            )
            try:
                self.rollback_snapshot = self._rollback_snapshot_factory(self.db_path)
            except BaseException:
                self._remove_owned_candidate_before_finalize()
                raise
            self._transition(MutationStage.PREPARED)
            return self
        except BaseException:
            self._release_writer(*sys.exc_info())
            raise

    def capture_post_write_database(self) -> str:
        if self._stage is MutationStage.PREPARED:
            # Backwards-compatible convenience for existing callers while
            # retaining an explicit, auditable state transition.
            self.mark_body_db_mutated()
        self._require_stage(MutationStage.BODY_DB_MUTATED)
        self.post_write_db_sha256 = generations.sha256_file(self.db_path).lower()
        generations.invalidate_generation_validation_cache()
        self._transition(MutationStage.POST_WRITE_SNAPSHOT)
        return self.post_write_db_sha256

    def mark_body_db_mutated(self) -> None:
        self._require_stage(MutationStage.PREPARED)
        self._transition(MutationStage.BODY_DB_MUTATED)

    def mark_candidate_synced(self) -> None:
        self._require_stage(MutationStage.POST_WRITE_SNAPSHOT)
        self._transition(MutationStage.CANDIDATE_SYNCED)

    def validate_candidate(self, validator: CandidateValidator) -> None:
        if self._stage is MutationStage.POST_WRITE_SNAPSHOT:
            # Compatibility for the first adopters; new workflows should call
            # mark_candidate_synced() after all operation-specific writes.
            self.mark_candidate_synced()
        self._require_stage(MutationStage.CANDIDATE_SYNCED)
        if self.candidate is None or self.post_write_db_sha256 is None:
            raise ProductionGenerationProtocolError("candidate validation state is incomplete")
        validator(self.candidate, self.post_write_db_sha256)
        self._transition(MutationStage.CANDIDATE_VALIDATED)

    def finalize_candidate(
        self,
        *,
        profile_versions: dict[str, object] | None = None,
    ) -> generations.RetrievalGenerationSnapshot:
        self._require_stage(MutationStage.CANDIDATE_VALIDATED)
        if self.candidate is None or self.post_write_db_sha256 is None:
            raise ProductionGenerationProtocolError("candidate finalization state is incomplete")
        self.finalized_generation = generations.finalize_candidate_generation(
            self.candidate,
            production_db_sha256=self.post_write_db_sha256,
            profile_versions=profile_versions,
        )
        self._transition(MutationStage.FINALIZED)
        return self.finalized_generation

    def begin_activation(self) -> None:
        self._require_stage(MutationStage.FINALIZED)
        if (
            self.previous_generation is None
            or self.finalized_generation is None
            or self.post_write_db_sha256 is None
        ):
            raise ProductionGenerationProtocolError("activation state is incomplete")
        try:
            generations.begin_generation_activation(
                self.previous_generation,
                self.finalized_generation,
                production_db_sha256=self.post_write_db_sha256,
                data_dir=self.data_dir,
            )
            self._activation_state_written = True
        except BaseException:
            self._activation_state_written = generations.activation_state_path(
                self.data_dir
            ).is_file()
            raise
        self._transition(MutationStage.ACTIVATING)

    def publish_active(self) -> None:
        self._require_stage(MutationStage.ACTIVATING)
        if self.finalized_generation is None:
            raise ProductionGenerationProtocolError("finalized generation is missing")
        try:
            generations.publish_active_generation(
                self.finalized_generation,
                data_dir=self.data_dir,
            )
            self._pointer_switched = True
        except BaseException:
            current_pointer = generations.read_active_pointer_bytes(data_dir=self.data_dir)
            self._pointer_switched = current_pointer != self.previous_pointer_bytes
            if self._pointer_switched:
                self._transition(MutationStage.POINTER_SWITCHED)
            raise
        self._transition(MutationStage.POINTER_SWITCHED)

    def verify_active(self, validator: ActiveValidator) -> None:
        self._require_stage(MutationStage.POINTER_SWITCHED)
        if self.finalized_generation is None or self.post_write_db_sha256 is None:
            raise ProductionGenerationProtocolError("active verification state is incomplete")
        generations.verify_generation_database_revision(
            self.finalized_generation,
            self.db_path,
        )
        resolved = generations.resolve_active_retrieval_generation(
            data_dir=self.data_dir,
            db_path=self.db_path,
            verify_fingerprints=True,
        )
        if (
            resolved.mode != "versioned"
            or resolved.generation_id != self.finalized_generation.generation_id
            or resolved.production_db_sha256.lower() != self.post_write_db_sha256
        ):
            raise ProductionGenerationMutationError(
                "published retrieval generation did not resolve to this transaction"
            )
        # Pass the explicit finalized snapshot, never a potentially stale ContextVar pin.
        validator(self.finalized_generation)
        self._transition(MutationStage.POST_SWITCH_VERIFIED)

    def clear_activation(self) -> None:
        self._require_stage(MutationStage.POST_SWITCH_VERIFIED)
        generations.clear_activation_state(data_dir=self.data_dir)
        self._activation_state_written = False
        self._transition(MutationStage.ACTIVATION_CLEARED)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        protocol_error: ProductionGenerationProtocolError | None = None
        rollback_error: ProductionGenerationRollbackError | None = None
        if exc is None and self._stage is not MutationStage.ACTIVATION_CLEARED:
            protocol_error = ProductionGenerationProtocolError(
                "mutation session exited before activation was completely verified and cleared"
            )
            exc_type = type(protocol_error)
            exc = protocol_error
            traceback = protocol_error.__traceback__
        try:
            if exc is not None and self._stage is MutationStage.ACTIVATION_CLEARED:
                # Durable activation is already committed.  A later response,
                # receipt, or transport failure must never roll the product
                # state back after the fail-closed marker has been cleared.
                self._discard_rollback_snapshot()
            elif exc is not None:
                rollback_error = self._rollback(exc)
            else:
                self._discard_rollback_snapshot()
        finally:
            # No error path may strand the process-wide writer barrier.
            self._release_writer(exc_type, exc, traceback)
        if rollback_error is not None:
            raise rollback_error from exc
        if protocol_error is not None:
            raise protocol_error
        return False

    def _rollback(
        self,
        original: BaseException,
    ) -> ProductionGenerationRollbackError | None:
        self._transition(MutationStage.ROLLING_BACK)
        if self._pointer_switched:
            try:
                self._pointer_restorer(
                    self.previous_pointer_bytes,
                    data_dir=self.data_dir,
                )
                if (
                    generations.read_active_pointer_bytes(data_dir=self.data_dir)
                    != self.previous_pointer_bytes
                ):
                    raise ProductionGenerationMutationError(
                        "active pointer rollback verification failed"
                    )
                self._pointer_switched = False
            except BaseException as cause:
                try:
                    pointer_was_restored = (
                        generations.read_active_pointer_bytes(data_dir=self.data_dir)
                        == self.previous_pointer_bytes
                    )
                except Exception:
                    pointer_was_restored = False
                if pointer_was_restored:
                    # os.replace can commit the rollback and then surface a
                    # directory-fsync error.  Exact pointer bytes are the
                    # authoritative commit result.
                    self._pointer_switched = False
                else:
                    self._mark_degraded(
                        error_code="active_pointer_rollback_failed",
                        publish_substage="active_pointer_rollback",
                    )
                    return ProductionGenerationRollbackError(
                        "active pointer rollback failed; matching new database and generation were retained",
                        stage=MutationStage.DEGRADED,
                        rollback_substage="active_pointer_rollback",
                        cause=cause,
                    )

        if self.rollback_snapshot is not None:
            try:
                self._rollback_snapshot_restorer(
                    self.db_path,
                    self.rollback_snapshot,
                )
                if generations.sha256_file(self.db_path).lower() != self.rollback_snapshot.sha256:
                    raise ProductionGenerationMutationError(
                        "production database rollback verification failed"
                    )
                generations.invalidate_generation_validation_cache()
            except BaseException as cause:
                self._mark_degraded(
                    error_code="production_db_rollback_failed",
                    publish_substage="production_db_rollback",
                )
                return ProductionGenerationRollbackError(
                    "production database rollback failed",
                    stage=MutationStage.DEGRADED,
                    rollback_substage="production_db_rollback",
                    cause=cause,
                )

        try:
            self._remove_owned_inactive_generation()
        except BaseException as cause:
            self._mark_degraded(
                error_code="generation_rollback_cleanup_failed",
                publish_substage="generation_rollback_cleanup",
            )
            return ProductionGenerationRollbackError(
                "owned inactive generation cleanup failed",
                stage=MutationStage.DEGRADED,
                rollback_substage="generation_rollback_cleanup",
                cause=cause,
            )

        try:
            if self.previous_generation is None:
                raise ProductionGenerationProtocolError("previous generation is missing")
            restored = generations.resolve_active_retrieval_generation(
                data_dir=self.data_dir,
                db_path=self.db_path,
                verify_fingerprints=True,
            )
            if (
                restored.mode != self.previous_generation.mode
                or restored.generation_id != self.previous_generation.generation_id
            ):
                raise ProductionGenerationMutationError(
                    "restored generation does not match the pre-write generation"
                )
            generations.verify_generation_database_revision(restored, self.db_path)
        except BaseException as cause:
            self._mark_degraded(
                error_code="generation_rollback_verification_failed",
                publish_substage="generation_rollback_verification",
            )
            return ProductionGenerationRollbackError(
                "rolled-back generation could not be verified",
                stage=MutationStage.DEGRADED,
                rollback_substage="generation_rollback_verification",
                cause=cause,
            )

        if self._activation_state_written:
            try:
                # The durable fail-closed marker is cleared only after the old
                # pointer, database, and generation have all been revalidated.
                generations.clear_activation_state(data_dir=self.data_dir)
                self._activation_state_written = False
            except BaseException as cause:
                self._mark_degraded(
                    error_code="activation_state_rollback_failed",
                    publish_substage="activation_state_rollback",
                )
                return ProductionGenerationRollbackError(
                    "activation state rollback failed",
                    stage=MutationStage.DEGRADED,
                    rollback_substage="activation_state_rollback",
                    cause=cause,
                )

        self._discard_rollback_snapshot()
        self._transition(MutationStage.ROLLED_BACK)
        return None

    def _mark_degraded(self, *, error_code: str, publish_substage: str) -> None:
        marker = generations.activation_state_path(self.data_dir)
        if marker.is_file() and self._pointer_switched:
            try:
                generations.mark_activation_degraded(
                    data_dir=self.data_dir,
                    error_code=error_code,
                    publish_substage=publish_substage,
                )
            except Exception:
                # Retaining an existing activating marker is itself durable
                # fail-closed state.
                pass
        coordinator = generations.PRODUCTION_GENERATION_COORDINATOR
        if not coordinator.degraded:
            coordinator.mark_degraded(error_code)
        self._transition(MutationStage.DEGRADED)

    def _remove_owned_candidate_before_finalize(self) -> None:
        if self.candidate is None:
            return
        self._remove_owned_tree(self.candidate.candidate_dir)

    def _remove_owned_inactive_generation(self) -> None:
        if self.candidate is None:
            return
        if (
            generations.read_active_pointer_bytes(data_dir=self.data_dir)
            != self.previous_pointer_bytes
        ):
            raise ProductionGenerationMutationError(
                "owned generation cannot be removed while the active pointer differs"
            )
        candidate_path = self.candidate.candidate_dir
        if candidate_path.exists() or candidate_path.is_symlink():
            self._remove_owned_tree(candidate_path)
        final_path = self.candidate.final_dir
        if final_path.exists() or final_path.is_symlink():
            if (
                self.finalized_generation is None
                or self.finalized_generation.generation_id != self.candidate.generation_id
                or self.finalized_generation.generation_dir.resolve(strict=False)
                != final_path.resolve(strict=False)
            ):
                raise ProductionGenerationMutationError(
                    "final generation ownership could not be proven"
                )
            self._remove_owned_tree(final_path)

    def _remove_owned_tree(self, path: Path) -> None:
        root = generations.generation_root(self.data_dir).resolve(strict=False)
        target = Path(path)
        if target.is_symlink():
            raise ProductionGenerationMutationError(
                "owned generation path unexpectedly became a symlink"
            )
        if target.parent.resolve(strict=False) != root:
            raise ProductionGenerationMutationError(
                "owned generation path escaped the generation root"
            )
        if target.exists():
            if not target.is_dir():
                raise ProductionGenerationMutationError(
                    "owned generation path is not a directory"
                )
            self._owned_tree_remover(target)

    def _discard_rollback_snapshot(self) -> None:
        if self.rollback_snapshot is None:
            return
        try:
            self.rollback_snapshot.path.unlink(missing_ok=True)
        except OSError:
            # A verified rollback artifact is safe to retain.  Cleanup failure
            # must not change an already committed or rolled-back result.
            return
        self.rollback_snapshot = None

    def _release_writer(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._writer_context is None:
            return
        writer = self._writer_context
        self._writer_context = None
        self._entered = False
        writer.__exit__(exc_type, exc, traceback)


__all__ = [
    "DatabaseRollbackSnapshot",
    "MutationStage",
    "ProductionGenerationMutationError",
    "ProductionGenerationMutationSession",
    "ProductionGenerationProtocolError",
    "ProductionGenerationRollbackError",
    "create_database_rollback_snapshot",
    "restore_database_rollback_snapshot",
]
