from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.paths import DATA_DIR
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models import Document, KnowledgeChunk
from app.services.embedding_service import (
    DEFAULT_MODEL_CACHE_DIR,
    BaseEmbedder,
    cosine_similarity,
    create_embedder,
)
from app.services.keyword_search_service import DEFAULT_SNIPPET_CHARS, build_pdf_open_url
from app.services.search_helpers import load_chunk_tags, load_related_note_titles, make_snippet


VECTOR_INDEX_DIR = DATA_DIR / "vector_index"
VECTOR_CHUNKS_PATH = VECTOR_INDEX_DIR / "chunks.jsonl"
VECTOR_MANIFEST_PATH = VECTOR_INDEX_DIR / "manifest.json"


class VectorIndexNotFoundError(FileNotFoundError):
    pass


class VectorIndexModelMismatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class VectorIndexManifest:
    index_dir: Path
    chunks_path: Path
    manifest_path: Path
    chunk_count: int
    embedding_model: str
    embedding_dimension: int
    embedder_type: str
    created_at: str
    source: str
    model_cache_dir: str | None
    normalize_embeddings: bool


@dataclass(frozen=True)
class VectorIndexRecord:
    chunk_id: int
    document_id: int
    document_type: str
    content_layer: str
    heading_path: str
    embedding: list[float]


@dataclass(frozen=True)
class VectorSearchHit:
    chunk_id: int
    document_id: int
    score: float


@dataclass(frozen=True)
class ScoredSearchResult:
    score: float
    document_id: int
    document_title: str
    document_type: str
    content_layer: str
    heading_path: str
    chunk_id: int
    chunk_text_snippet: str
    pdf_path: str | None
    pdf_page_start: int | None
    pdf_open_url: str | None
    zotero_open_url: str | None
    related_note_titles: list[str]
    chunk_tags: list[str]


def rebuild_vector_index(
    index_dir: Path = VECTOR_INDEX_DIR,
    embedder: BaseEmbedder | None = None,
    embedder_name: str = "hash-text-v1",
    model_cache_dir: str | Path | None = None,
) -> VectorIndexManifest:
    init_db()
    active_embedder = embedder or create_embedder(
        embedder_name=embedder_name,
        cache_folder=model_cache_dir or DEFAULT_MODEL_CACHE_DIR,
    )
    chunks_path = index_dir / "chunks.jsonl"
    manifest_path = index_dir / "manifest.json"
    index_dir.mkdir(parents=True, exist_ok=True)

    chunk_count = 0
    with SessionLocal() as session, chunks_path.open("w", encoding="utf-8") as output:
        rows = session.execute(
            select(Document, KnowledgeChunk)
            .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
            .order_by(KnowledgeChunk.id)
        ).all()
        for document, chunk in rows:
            text = chunk.chunk_text.strip()
            if not text:
                continue
            record = {
                "chunk_id": chunk.id,
                "document_id": document.id,
                "document_type": document.document_type,
                "content_layer": document.content_layer,
                "heading_path": chunk.heading_path,
                "embedding": active_embedder.embed_text(text),
            }
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            chunk_count += 1

    created_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    cache_dir_text = (
        str(model_cache_dir or DEFAULT_MODEL_CACHE_DIR)
        if active_embedder.name != "hash-text-v1"
        else None
    )
    manifest = VectorIndexManifest(
        index_dir=index_dir,
        chunks_path=chunks_path,
        manifest_path=manifest_path,
        chunk_count=chunk_count,
        embedding_model=active_embedder.name,
        embedding_dimension=active_embedder.dimension,
        embedder_type=active_embedder.__class__.__name__,
        created_at=created_at,
        source="sqlite:knowledge_chunks",
        model_cache_dir=cache_dir_text,
        normalize_embeddings=True,
    )
    manifest_path.write_text(
        json.dumps(
            {
                "chunk_count": manifest.chunk_count,
                "embedding_model": manifest.embedding_model,
                "embedding_dimension": manifest.embedding_dimension,
                "embedder_type": manifest.embedder_type,
                "created_at": manifest.created_at,
                "source": manifest.source,
                "model_cache_dir": manifest.model_cache_dir,
                "normalize_embeddings": manifest.normalize_embeddings,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest


def load_vector_manifest(index_dir: Path = VECTOR_INDEX_DIR) -> dict[str, object]:
    manifest_path = index_dir / "manifest.json"
    chunks_path = index_dir / "chunks.jsonl"
    if not chunks_path.exists() or not manifest_path.exists():
        raise VectorIndexNotFoundError(
            f"Vector index not found at {index_dir}. Run rebuild-vector-index first."
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def load_vector_index(index_dir: Path = VECTOR_INDEX_DIR) -> list[VectorIndexRecord]:
    chunks_path = index_dir / "chunks.jsonl"
    manifest_path = index_dir / "manifest.json"
    if not chunks_path.exists() or not manifest_path.exists():
        raise VectorIndexNotFoundError(
            f"Vector index not found at {index_dir}. Run rebuild-vector-index first."
        )

    records: list[VectorIndexRecord] = []
    with chunks_path.open("r", encoding="utf-8") as input_file:
        for line in input_file:
            if not line.strip():
                continue
            raw = json.loads(line)
            records.append(
                VectorIndexRecord(
                    chunk_id=int(raw["chunk_id"]),
                    document_id=int(raw["document_id"]),
                    document_type=str(raw["document_type"]),
                    content_layer=str(raw["content_layer"]),
                    heading_path=str(raw["heading_path"]),
                    embedding=[float(value) for value in raw["embedding"]],
                )
            )
    return records


def vector_search_hits(
    query: str,
    limit: int = 10,
    index_dir: Path = VECTOR_INDEX_DIR,
    embedder: BaseEmbedder | None = None,
    embedder_name: str | None = None,
    model_cache_dir: str | Path | None = None,
) -> list[VectorSearchHit]:
    normalized_query = query.strip()
    if not normalized_query:
        return []

    manifest = load_vector_manifest(index_dir=index_dir)
    index_model = str(manifest.get("embedding_model", ""))
    requested_embedder = embedder_name or index_model or "hash-text-v1"
    if requested_embedder != index_model:
        raise VectorIndexModelMismatchError(
            "Vector index embedding model mismatch: "
            f"index uses {index_model!r}, requested {requested_embedder!r}. "
            f"Run rebuild-vector-index --embedder {requested_embedder} first."
        )

    active_embedder = embedder or create_embedder(
        embedder_name=requested_embedder,
        cache_folder=model_cache_dir or manifest.get("model_cache_dir") or DEFAULT_MODEL_CACHE_DIR,
    )
    query_embedding = active_embedder.embed_text(normalized_query)
    scored_records = [
        VectorSearchHit(
            chunk_id=record.chunk_id,
            document_id=record.document_id,
            score=cosine_similarity(query_embedding, record.embedding),
        )
        for record in load_vector_index(index_dir=index_dir)
    ]
    scored_records = [hit for hit in scored_records if hit.score > 0.0]
    scored_records.sort(key=lambda hit: hit.score, reverse=True)
    return scored_records[: max(1, limit)]


def vector_search(
    query: str,
    limit: int = 10,
    document_type: str | None = None,
    content_layer: str | None = None,
    read_status: str | None = None,
    index_dir: Path = VECTOR_INDEX_DIR,
    embedder_name: str | None = None,
    model_cache_dir: str | Path | None = None,
) -> list[ScoredSearchResult]:
    hits = vector_search_hits(
        query=query,
        limit=max(limit * 10, 50),
        index_dir=index_dir,
        embedder_name=embedder_name,
        model_cache_dir=model_cache_dir,
    )
    return hydrate_scored_results(
        query=query,
        chunk_scores={hit.chunk_id: hit.score for hit in hits},
        limit=limit,
        document_type=document_type,
        content_layer=content_layer,
        read_status=read_status,
    )


def hydrate_scored_results(
    query: str,
    chunk_scores: dict[int, float],
    limit: int = 10,
    document_type: str | None = None,
    content_layer: str | None = None,
    read_status: str | None = None,
) -> list[ScoredSearchResult]:
    if not chunk_scores:
        return []

    with SessionLocal() as session:
        rows = _load_chunk_rows(
            session=session,
            chunk_ids=list(chunk_scores),
            document_type=document_type,
            content_layer=content_layer,
            read_status=read_status,
        )
        related_note_titles = load_related_note_titles(session, [chunk.id for _, chunk in rows])
        chunk_tags = load_chunk_tags(session, [chunk.id for _, chunk in rows])
        results = [
            ScoredSearchResult(
                score=chunk_scores[chunk.id],
                document_id=document.id,
                document_title=document.title,
                document_type=document.document_type,
                content_layer=document.content_layer,
                heading_path=chunk.heading_path,
                chunk_id=chunk.id,
                chunk_text_snippet=make_snippet(
                    chunk.chunk_text,
                    query,
                    DEFAULT_SNIPPET_CHARS,
                    match_tokens=True,
                ),
                pdf_path=chunk.pdf_path or document.pdf_path,
                pdf_page_start=chunk.pdf_page_start,
                pdf_open_url=build_pdf_open_url(chunk.pdf_path or document.pdf_path, chunk.pdf_page_start),
                zotero_open_url=chunk.zotero_open_url,
                related_note_titles=related_note_titles.get(chunk.id, []),
                chunk_tags=chunk_tags.get(chunk.id, []),
            )
            for document, chunk in rows
        ]
    results.sort(key=lambda result: result.score, reverse=True)
    return results[: max(1, limit)]


def _load_chunk_rows(
    session: Session,
    chunk_ids: list[int],
    document_type: str | None,
    content_layer: str | None,
    read_status: str | None,
) -> list[tuple[Document, KnowledgeChunk]]:
    statement = (
        select(Document, KnowledgeChunk)
        .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
        .where(KnowledgeChunk.id.in_(chunk_ids))
    )
    if document_type:
        statement = statement.where(Document.document_type == document_type)
    if content_layer:
        statement = statement.where(Document.content_layer == content_layer)
    if read_status:
        statement = statement.where(Document.read_status == read_status)
    return list(session.execute(statement).all())

