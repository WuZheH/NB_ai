import pytest
from pathlib import Path
import json, hashlib, sqlite3
from types import SimpleNamespace

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

def _production_fixture(tmp_path: Path, monkeypatch, statuses):
    db=tmp_path/"db.sqlite"; index=tmp_path/"fts.db"; manifest=tmp_path/"fts.json"
    with sqlite3.connect(db) as c: c.execute("CREATE TABLE marker(value TEXT)"); c.execute("INSERT INTO marker VALUES('x')"); c.commit()
    fts_index_service._build_database(index, [])
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    manifest.write_text(json.dumps({"production_db_sha256": before, "fragment_count": 0, "index_content_hash": hashlib.sha256(index.read_bytes()).hexdigest(), "index_file_bytes": index.stat().st_size, "zotero_snapshot_sha256": "", "local_markdown_aggregate_hash": "", "source_type_counts": {}, "origin_kind_counts": {}, "duplicate_group_count": 0}), encoding="utf-8")
    monkeypatch.setattr(fts_index_service, "DEFAULT_DB_PATH", db); monkeypatch.setattr(fts_index_service, "DEFAULT_INDEX_PATH", index); monkeypatch.setattr(fts_index_service, "DEFAULT_MANIFEST_PATH", manifest)
    monkeypatch.setattr(fts_index_service, "RetrievalSourceRegistry", lambda *a,**k: SimpleNamespace(read=lambda **_: SimpleNamespace(fragments=[])))
    monkeypatch.setattr(fts_index_service, "source_fingerprints", lambda **_: {"zotero_snapshot_sha256":"", "local_markdown_aggregate_hash":""})
    monkeypatch.setattr(fts_index_service, "_refresh_manifest_after_document_change", lambda **_: (manifest.write_text(json.dumps({"production_db_sha256": hashlib.sha256(db.read_bytes()).hexdigest(), "fragment_count":0, "index_content_hash":hashlib.sha256(index.read_bytes()).hexdigest(), "index_file_bytes":index.stat().st_size}), encoding="utf-8") or json.loads(manifest.read_text(encoding="utf-8"))))
    monkeypatch.setattr(fts_index_service, "get_index_status", lambda *a,**k: statuses.pop(0))
    return db,index,manifest,before

def test_production_upsert_post_commit_failure_restores_exact_bytes(tmp_path, monkeypatch):
    statuses=[{"status":"source_drift","reasons":["production_db_sha256_changed"]},{"status":"broken","ready":False}]
    db,index,manifest,before=_production_fixture(tmp_path,monkeypatch,statuses); ib=index.read_bytes(); mb=manifest.read_bytes()
    with pytest.raises(RuntimeError, match="production FTS not ready"):
        fts_index_service.upsert_document_retrieval_fts(document_id=1,index_path=index,manifest_path=manifest,research_db_path=db,allow_production=True,expected_before_db_sha256=before,expected_after_db_sha256=before)
    assert index.read_bytes()==ib and manifest.read_bytes()==mb

@pytest.mark.parametrize("persistent", [False, True])
def test_production_upsert_restore_permission_paths(tmp_path, monkeypatch, persistent):
    statuses=[{"status":"source_drift","reasons":["production_db_sha256_changed"]},{"status":"broken","ready":False}]
    db,index,manifest,before=_production_fixture(tmp_path,monkeypatch,statuses); ib=index.read_bytes(); mb=manifest.read_bytes(); real=fts_index_service.os.replace; calls=[]
    def replace(src,dst):
        if str(src).endswith(".restore"):
            calls.append(1)
            if persistent or len(calls)==1: raise PermissionError("locked")
        return real(src,dst)
    monkeypatch.setattr(fts_index_service.os,"replace",replace); monkeypatch.setattr(fts_index_service.time,"sleep",lambda _:None)
    expected = "production FTS document upsert rollback failed" if persistent else "production FTS not ready after upsert"
    with pytest.raises(RuntimeError, match=expected):
        fts_index_service.upsert_document_retrieval_fts(document_id=1,index_path=index,manifest_path=manifest,research_db_path=db,allow_production=True,expected_before_db_sha256=before,expected_after_db_sha256=before)
    if persistent:
        backups = list(tmp_path.glob("*.backup"))
        assert len(backups) >= 2
        payloads = [path.read_bytes() for path in backups]
        assert ib in payloads
        assert mb in payloads
    else:
        assert len(calls) >= 2
        assert index.read_bytes() == ib
        assert manifest.read_bytes() == mb

@pytest.mark.parametrize("suffix", [".db", ".json", ".manifest"])
def test_restore_file_verified_preserves_exact_bytes(tmp_path: Path, suffix: str):
    backup = tmp_path / ("backup" + suffix); target = tmp_path / ("target" + suffix); data = (suffix * 3).encode(); backup.write_bytes(data); target.write_bytes(b"x")
    import hashlib
    fts_index_service._restore_file_verified(backup_path=backup, target_path=target, expected_sha256=hashlib.sha256(data).hexdigest(), expected_size=len(data))
    assert target.read_bytes() == data
