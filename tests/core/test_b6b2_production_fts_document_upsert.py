import pytest
from pathlib import Path

from app.services.retrieval import fts_index_service


def test_production_fts_requires_explicit_opt_in():
    with pytest.raises(ValueError, match="explicit opt-in"):
        fts_index_service.upsert_document_retrieval_fts(
            document_id=1,
            index_path=fts_index_service.DEFAULT_INDEX_PATH,
            manifest_path=fts_index_service.DEFAULT_MANIFEST_PATH,
            research_db_path=fts_index_service.DEFAULT_DB_PATH,
        )

@pytest.mark.parametrize("value", ["", "abc", "g" * 63, "z" * 64])
def test_restore_file_verified_rejects_invalid_backup(tmp_path: Path, value: str):
    with pytest.raises(RuntimeError):
        fts_index_service._restore_file_verified(backup_path=tmp_path / "missing", target_path=tmp_path / "target", expected_sha256=value, expected_size=0)

def test_restore_file_verified_roundtrip(tmp_path: Path):
    backup = tmp_path / "backup"; target = tmp_path / "target"; backup.write_bytes(b"original"); target.write_bytes(b"changed")
    import hashlib
    fts_index_service._restore_file_verified(backup_path=backup, target_path=target, expected_sha256=hashlib.sha256(b"original").hexdigest(), expected_size=8)
    assert target.read_bytes() == b"original"

def test_restore_file_verified_retries_permission_error(tmp_path: Path, monkeypatch):
    backup=tmp_path/"backup"; target=tmp_path/"target"; backup.write_bytes(b"original"); target.write_bytes(b"changed")
    import hashlib
    real = fts_index_service.os.replace; calls=[]
    def flaky(src,dst):
        calls.append(1)
        if len(calls)==1: raise PermissionError("lock")
        return real(src,dst)
    monkeypatch.setattr(fts_index_service.os, "replace", flaky); monkeypatch.setattr(fts_index_service.time, "sleep", lambda _: None)
    fts_index_service._restore_file_verified(backup_path=backup,target_path=target,expected_sha256=hashlib.sha256(b"original").hexdigest(),expected_size=8)
    assert len(calls)==2 and target.read_bytes()==b"original"

def test_restore_file_verified_persistent_lock_fails_closed(tmp_path: Path, monkeypatch):
    backup=tmp_path/"backup"; target=tmp_path/"target"; backup.write_bytes(b"original"); target.write_bytes(b"changed")
    import hashlib
    monkeypatch.setattr(fts_index_service.os, "replace", lambda *_: (_ for _ in ()).throw(PermissionError("lock"))); monkeypatch.setattr(fts_index_service.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError):
        fts_index_service._restore_file_verified(backup_path=backup,target_path=target,expected_sha256=hashlib.sha256(b"original").hexdigest(),expected_size=8)
    assert backup.read_bytes()==b"original"

@pytest.mark.parametrize("suffix", [".db", ".json", ".manifest"])
def test_restore_file_verified_preserves_exact_bytes(tmp_path: Path, suffix: str):
    backup = tmp_path / ("backup" + suffix); target = tmp_path / ("target" + suffix); data = (suffix * 3).encode(); backup.write_bytes(data); target.write_bytes(b"x")
    import hashlib
    fts_index_service._restore_file_verified(backup_path=backup, target_path=target, expected_sha256=hashlib.sha256(data).hexdigest(), expected_size=len(data))
    assert target.read_bytes() == data
