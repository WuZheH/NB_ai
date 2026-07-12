# Search Pipeline

## High-quality search

High-quality retrieval has a fixed two-model pipeline:

```text
query
-> Qwen3-Embedding-0.6B
-> semantic recall
-> Qwen3-Reranker-0.6B
-> legacy final ranking
```

The embedding service owns query/passage prefixes, normalization, dimensions,
and batching. Semantic object and passage recall produce candidates for the
reranker. The reranker score direction, candidate truncation, object boost, and
legacy final-ranking rules are preserved by the high-quality search service.
Recall and result limits come from the existing service defaults and request
contract.

When the persisted vector source is unavailable or stale, the existing
`fallback_in_memory` path remains explicit in response metadata. It still uses
the embedding and reranker models; it is not an FTS fallback.

## FTS search

FTS5/BM25 is an independent auxiliary retrieval mode with its own database,
manifest, status endpoint, and maintenance scripts. High-quality search never
silently calls FTS. A model/vector problem must remain visible to the caller
rather than changing retrieval modes.

## Runtime assets

The model directories, FTS database, vector index/store, and production SQLite
database are local runtime assets. Status and search operations are read-only;
build, sync, migration, or repair scripts must be invoked explicitly.
