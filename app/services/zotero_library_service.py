from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.services import zotero_source_cache_service


def list_parent_items(*, query: str | None = None, limit: int = 20) -> dict[str, Any]:
    config = zotero_source_cache_service._load_config()
    snapshot = zotero_source_cache_service._project_path(config["zotero_db_snapshot"]).resolve(strict=False)
    if not snapshot.is_file():
        raise RuntimeError("zotero_snapshot_missing")
    q = (query or "").strip().casefold()
    limit = max(1, min(int(limit), 50))
    with sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
          SELECT parent.key AS item_key,
                 COALESCE(item_types.typeName, 'unknown') AS item_type,
                 COALESCE(title_values.value, '') AS title,
                 COUNT(DISTINCT att.itemID) AS attachment_count,
                 SUM(CASE WHEN lower(COALESCE(att.contentType,''))='application/pdf' OR lower(COALESCE(att.path,'')) LIKE '%.pdf' THEN 1 ELSE 0 END) AS pdf_count,
                 SUM((SELECT COUNT(*) FROM itemAnnotations ia WHERE ia.parentItemID=att.itemID)) AS annotation_count,
                 (SELECT COUNT(*) FROM itemNotes n WHERE n.parentItemID=parent.itemID OR n.parentItemID IN (SELECT itemID FROM itemAttachments WHERE parentItemID=parent.itemID)) AS child_note_count
          FROM items parent
          LEFT JOIN itemData title_data ON title_data.itemID=parent.itemID AND title_data.fieldID=(SELECT fieldID FROM fields WHERE fieldName='title' LIMIT 1)
          LEFT JOIN itemDataValues title_values ON title_values.valueID=title_data.valueID
          LEFT JOIN itemTypes item_types ON item_types.itemTypeID=parent.itemTypeID
          LEFT JOIN itemAttachments att ON att.parentItemID=parent.itemID
          WHERE parent.itemID NOT IN (SELECT itemID FROM itemAttachments)
          GROUP BY parent.itemID, parent.key, title_values.value
          ORDER BY lower(title) ASC, parent.key ASC
        """).fetchall()
    items=[]
    for row in rows:
        title=str(row["title"] or "")
        if q and q not in title.casefold():
            continue
        attachments=int(row["attachment_count"] or 0)
        with sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True) as attachment_conn:
            attachment_conn.row_factory = sqlite3.Row
            choices=[]
            for att in attachment_conn.execute("SELECT ia.itemID, ia.key, ia.path, ia.contentType FROM itemAttachments ia WHERE ia.parentItemID=(SELECT itemID FROM items WHERE key=?) ORDER BY ia.key", (row["item_key"],)):
                raw=str(att["path"] or "")
                resolved, supported = zotero_source_cache_service._resolve_attachment_path(str(att["key"]), raw, Path(config["zotero_data_dir"]), Path(config["zotero_storage_root"]))
                choices.append({"zotero_attachment_key":str(att["key"]),"file_name":Path(raw.removeprefix("storage:")).name if raw else None,"path_exists":bool(supported and resolved and Path(resolved).is_file()),"content_type":att["contentType"]})
        items.append({"kind":"zotero","document_id":None,"title":title,"item_type":str(row["item_type"] or "unknown"),"zotero_item_key":str(row["item_key"]),"has_pdf":int(row["pdf_count"] or 0)>0,"attachment_count":attachments,"attachment_choices":choices,"annotation_count":int(row["annotation_count"] or 0),"child_note_count":int(row["child_note_count"] or 0),"duplicate_status":"not_evaluated","status":"available"})
        if len(items)>=limit: break
    return {"status":"ok","scope":"zotero","count":len(items),"items":items,"truncated":False}
